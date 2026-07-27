# YouTube と TikTok から自分の動画の数値を集めて data.json に書き出す。
#
# 動画とカテゴリの紐付けは、short-factory がアップロード時に埋め込む
# 目印タグ（sf-bucket-travel など）から復元する。
# YouTubeのタグは視聴者には表示されないので、これで
# 「どのカテゴリが伸びるか」をリポジトリ間の同期なしに集計できる。
#
# 数値が取れなくても落とさない。取れたぶんだけ書いて、
# 取れなかった理由を data.json の notes に残す（静かに空にしない）。
import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone

import requests

GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
YT_SEARCH = "https://www.googleapis.com/youtube/v3/search"
YT_VIDEOS = "https://www.googleapis.com/youtube/v3/videos"
YT_CHANNELS = "https://www.googleapis.com/youtube/v3/channels"
YT_ANALYTICS = "https://youtubeanalytics.googleapis.com/v2/reports"
TIKTOK_VIDEO_LIST = "https://open.tiktokapis.com/v2/video/list/"

OUT = os.path.join(os.path.dirname(__file__), "data.json")
notes: list[str] = []


# ============================== 目印タグの解読 ==============================
def parse_markers(tags: list[str]) -> dict:
    """short-factory が埋めた目印タグから、カテゴリと素材内訳を取り出す"""
    out: dict = {"bucket": None, "category": None, "real": None, "ai": None}
    for t in tags or []:
        if t.startswith("sf-bucket-"):
            out["bucket"] = t.removeprefix("sf-bucket-")
        elif t.startswith("sf-cat-"):
            out["category"] = t.removeprefix("sf-cat-").replace("-", " ")
        elif t.startswith("sf-real-"):
            out["real"] = int(t.removeprefix("sf-real-") or 0)
        elif t.startswith("sf-ai-"):
            out["ai"] = int(t.removeprefix("sf-ai-") or 0)
    return out


# ============================== YouTube ==============================
def yt_token() -> str | None:
    cid = os.getenv("YOUTUBE_CLIENT_ID")
    secret = os.getenv("YOUTUBE_CLIENT_SECRET")
    refresh = os.getenv("YOUTUBE_REFRESH_TOKEN")
    if not (cid and secret and refresh):
        notes.append("YouTube: OAuth情報が未設定のため取得をスキップしました")
        return None
    r = requests.post(GOOGLE_TOKEN, timeout=30, data={
        "client_id": cid, "client_secret": secret,
        "refresh_token": refresh, "grant_type": "refresh_token",
    })
    if r.status_code != 200:
        notes.append(f"YouTube: トークン更新に失敗（HTTP {r.status_code}）"
                     f" {r.text[:120]}")
        return None
    return r.json()["access_token"]


def yt_uploads_playlist(token: str) -> str | None:
    r = requests.get(YT_CHANNELS, timeout=30,
                     headers={"Authorization": f"Bearer {token}"},
                     params={"part": "contentDetails,statistics", "mine": "true"})
    if r.status_code != 200:
        notes.append(f"YouTube: チャンネル取得に失敗（HTTP {r.status_code}）")
        return None
    items = r.json().get("items") or []
    if not items:
        notes.append("YouTube: このアカウントにチャンネルが見つかりません")
        return None
    return items[0]["contentDetails"]["relatedPlaylists"]["uploads"]


def yt_video_ids(token: str, playlist_id: str, limit: int = 200) -> list[str]:
    """アップロード済み動画のIDを新しい順に集める"""
    ids: list[str] = []
    page = None
    while len(ids) < limit:
        r = requests.get("https://www.googleapis.com/youtube/v3/playlistItems",
                         timeout=30, headers={"Authorization": f"Bearer {token}"},
                         params={"part": "contentDetails", "playlistId": playlist_id,
                                 "maxResults": 50, "pageToken": page})
        if r.status_code != 200:
            notes.append(f"YouTube: 動画一覧の取得に失敗（HTTP {r.status_code}）")
            break
        data = r.json()
        ids += [i["contentDetails"]["videoId"] for i in data.get("items", [])]
        page = data.get("nextPageToken")
        if not page:
            break
    return ids[:limit]


def yt_videos(token: str, ids: list[str]) -> list[dict]:
    """動画のタイトル・再生数・タグをまとめて取得（50件ずつ）"""
    out = []
    for i in range(0, len(ids), 50):
        chunk = ids[i:i + 50]
        r = requests.get(YT_VIDEOS, timeout=30,
                         headers={"Authorization": f"Bearer {token}"},
                         params={"part": "snippet,statistics,status",
                                 "id": ",".join(chunk)})
        if r.status_code != 200:
            notes.append(f"YouTube: 動画詳細の取得に失敗（HTTP {r.status_code}）")
            break
        for v in r.json().get("items", []):
            sn, st = v["snippet"], v.get("statistics", {})
            m = parse_markers(sn.get("tags", []))
            out.append({
                "platform": "youtube",
                "id": v["id"],
                "url": f"https://youtube.com/shorts/{v['id']}",
                "title": sn["title"],
                "published_at": sn["publishedAt"],
                "privacy": v.get("status", {}).get("privacyStatus"),
                "views": int(st.get("viewCount", 0)),
                "likes": int(st.get("likeCount", 0)),
                "comments": int(st.get("commentCount", 0)),
                **m,
            })
    return out


def yt_retention(token: str, ids: list[str]) -> dict[str, dict]:
    """
    視聴維持率を動画ごとに取得する。
    ショートは「最後まで見られたか」が伸びを左右するので、再生数より重要。
    """
    if not ids:
        return {}
    start = (date.today() - timedelta(days=365)).isoformat()
    r = requests.get(YT_ANALYTICS, timeout=60,
                     headers={"Authorization": f"Bearer {token}"},
                     params={
                         "ids": "channel==MINE",
                         "startDate": start,
                         "endDate": date.today().isoformat(),
                         "metrics": "views,estimatedMinutesWatched,"
                                    "averageViewDuration,averageViewPercentage",
                         "dimensions": "video",
                         "filters": "video==" + ",".join(ids[:200]),
                         "maxResults": 200,
                     })
    if r.status_code != 200:
        notes.append(f"YouTube Analytics: 取得に失敗（HTTP {r.status_code}）。"
                     "反映まで1〜2日かかることがあります")
        return {}
    data = r.json()
    cols = [h["name"] for h in data.get("columnHeaders", [])]
    out = {}
    for row in data.get("rows", []):
        d = dict(zip(cols, row))
        out[d["video"]] = {
            "watch_minutes": round(d.get("estimatedMinutesWatched", 0), 1),
            "avg_view_seconds": round(d.get("averageViewDuration", 0), 1),
            "avg_view_percent": round(d.get("averageViewPercentage", 0), 1),
        }
    return out


def collect_youtube() -> list[dict]:
    token = yt_token()
    if not token:
        return []
    playlist = yt_uploads_playlist(token)
    if not playlist:
        return []
    ids = yt_video_ids(token, playlist)
    if not ids:
        notes.append("YouTube: まだ動画が1本もありません")
        return []
    videos = yt_videos(token, ids)
    retention = yt_retention(token, ids)
    for v in videos:
        v.update(retention.get(v["id"], {}))
    return videos


# ============================== TikTok ==============================
def collect_tiktok() -> list[dict]:
    """
    TikTokの数値を取得する。
    video.list スコープの審査が通っていないと403になるので、
    その場合は空で返し理由をnotesに残す（手入力に切り替えられるように）。
    """
    token = os.getenv("TIKTOK_ACCESS_TOKEN")
    if not token:
        notes.append("TikTok: アクセストークン未設定のため取得をスキップしました")
        return []
    fields = ("id,title,video_description,create_time,"
              "view_count,like_count,comment_count,share_count")
    out, cursor = [], None
    for _ in range(10):  # 最大200件
        body = {"max_count": 20}
        if cursor:
            body["cursor"] = cursor
        r = requests.post(TIKTOK_VIDEO_LIST, timeout=30,
                          headers={"Authorization": f"Bearer {token}",
                                   "Content-Type": "application/json"},
                          params={"fields": fields}, json=body)
        if r.status_code != 200:
            notes.append(f"TikTok: 取得に失敗（HTTP {r.status_code}）。"
                         "video.list スコープの審査が必要な場合があります")
            break
        data = r.json().get("data", {})
        for v in data.get("videos", []):
            out.append({
                "platform": "tiktok",
                "id": v.get("id"),
                "url": f"https://www.tiktok.com/@me/video/{v.get('id')}",
                "title": v.get("title") or v.get("video_description", "")[:80],
                "published_at": datetime.fromtimestamp(
                    v.get("create_time", 0), tz=timezone.utc).isoformat(),
                "views": v.get("view_count", 0),
                "likes": v.get("like_count", 0),
                "comments": v.get("comment_count", 0),
                "shares": v.get("share_count", 0),
                # TikTokにはタグを埋められないので、カテゴリはタイトル一致でYouTube側から補う
                "bucket": None, "category": None,
            })
        if not data.get("has_more"):
            break
        cursor = data.get("cursor")
    return out


def backfill_tiktok_categories(tiktok: list[dict], youtube: list[dict]) -> None:
    """TikTok動画のカテゴリを、同じタイトルのYouTube動画から補う"""
    def norm(s: str) -> str:
        return re.sub(r"[^a-z0-9]", "", (s or "").lower())[:40]

    by_title = {norm(v["title"]): v for v in youtube if v.get("bucket")}
    for v in tiktok:
        src = by_title.get(norm(v["title"]))
        if src:
            v["bucket"] = src["bucket"]
            v["category"] = src["category"]


# ============================== 出力 ==============================
def main() -> int:
    youtube = collect_youtube()
    tiktok = collect_tiktok()
    backfill_tiktok_categories(tiktok, youtube)

    videos = sorted(youtube + tiktok,
                    key=lambda v: v.get("published_at") or "", reverse=True)
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "videos": videos,
        "notes": notes,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)

    print(f"YouTube {len(youtube)}本 / TikTok {len(tiktok)}本 → {OUT}")
    for n in notes:
        print(f"  ⚠️  {n}", file=sys.stderr)
    # 取得できなくても失敗にはしない（投稿前は0本が正常なため）
    return 0


if __name__ == "__main__":
    sys.exit(main())
