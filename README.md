# short-factory-stats

[short-factory](https://github.com/taisuzu/short-factory)（非公開）で作った
ショート動画の成績を見るための、自分専用のページ。

- **公開URL**: https://taisuzu.github.io/short-factory-stats/
- 中身は自分の動画のタイトルと再生数のみ。パイプラインのコードとプロンプトは非公開のまま
- 検索避け（`noindex`）済み

## 何が見えるか

| | |
|---|---|
| 投稿本数 / 総再生数 | 全体の規模 |
| 平均視聴維持率 | ショートで一番効く指標 |
| 1000再生あたり原価 | 制作費13.2円/本 ÷ 再生数。収益化ラインの目安 |
| **カテゴリ別の平均再生数** | 観光/文化/商品/その他 のどれが伸びるか |

カテゴリ別が本題。`short-factory` の配分（観光6:文化2:商品1:その他1）を
実データで見直すために作った。

## 仕組み

```
GitHub Actions（毎日9時JST）
  → YouTube Data API / Analytics API / TikTok API から取得
  → data.json にコミット
  → GitHub Pages が index.html と一緒に配信
```

動画とカテゴリの紐付けは、short-factory がアップロード時に埋める
目印タグ（`sf-bucket-travel` など）から復元している。
YouTubeのタグは視聴者には表示されないので、
リポジトリ間でデータを同期せずに集計できる。

TikTokにはタグを埋められないため、同じタイトルのYouTube動画から補完する。

## 必要なシークレット

リポジトリの Settings → Secrets and variables → Actions に登録する。

| シークレット | 取得元 |
|---|---|
| `YOUTUBE_CLIENT_ID` | Google Cloud のOAuthクライアント |
| `YOUTUBE_CLIENT_SECRET` | 同上 |
| `YOUTUBE_REFRESH_TOKEN` | short-factory の `tools/youtube_auth.py` |
| `TIKTOK_ACCESS_TOKEN` | TikTok開発者ポータル（任意） |

未設定でもエラーにはならない。取得できなかった理由がページ上部に出る。
