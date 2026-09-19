# 桃源郷 Core / きんぱつくん

Discordコミュニティ「桃源郷」専用BotのCoreです。

## 実装済み

- `/ping` 接続確認
- `/balance` COIN残高
- `/daily` 1日1回のCOIN付与
- `/pay` 個人間送金 + 10%手数料
- `/profile` 簡易プロフィール
- `/ticket` Ticket作成
- `/apply` 入郷申請
- `/approve` 運営による正式住人化 + 100 COIN
- `/reject` 入郷申請却下
- `/cheer` CHEER付与
- `/jobxp` JOB XP付与
- `/stats` 運営向け統計
- 動的VC作成
- 監査ログ
- SQLiteによる永続化（ローカル/永続ディスク向け）

## セキュリティ

Bot TokenやAPIキーはGitHubに書かず、Renderなどの環境変数に設定してください。

## 起動

環境変数 `DISCORD_TOKEN` と `GUILD_ID` を設定して:

```bash
python bot.py
```

## Discord Developer Portal

- Server Members Intent: ON
- Message Content Intent: ON
- Presence Intent: OFFでOK

## Render

Background Workerで起動する場合:

- Build Command: `pip install -r requirements.txt`
- Start Command: `python bot.py`

SQLiteを使う場合、再起動で消えない永続ディスクを使ってください。
将来的にはPostgreSQLへ切り替える構成を推奨します。
