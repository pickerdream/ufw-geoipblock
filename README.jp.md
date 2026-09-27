# geoipblock

`xtables-addons` と `UFW` を使用して、LinuxサーバーのGeoIPフィルタリング（国別ブロック）を自動化するスクリプトです。UFWの `before.rules` にルールを注入することで、特定のポートに対して指定した国以外からのアクセスを制限します。

## 🛡️ アーキテクチャ

既存の設定を上書きすることなく、UFWの `before.rules` に以下の多層フィルタリングブロックを追加します：

1.  **Phase 1: ipset ブラックリスト**: `persistent_offenders` ipset に登録されたIPからの通信を高速にドロップします。
2.  **Phase 2: ステート管理**: `conntrack` (`RELATED,ESTABLISHED`) を使用し、確立済みのセッションを維持します。
3.  **Phase 3: ローカルネットワーク**: ホストが直接接続しているローカルサブネットからの通信を自動検知して許可します。
4.  **Phase 4: GeoIP フィルタリング**: `xt_geoip` を使用して国判別を行います。許可されていない国からの TCP/UDP 通信は、レートリミット付きでログに記録され、ドロップされます。

## ✨ 特徴

- **自動ロールバック (Dead Man's Switch)**: インストール後3分以内に `geoipblock-confirm` コマンドを実行しない場合、設定が自動的に元に戻ります。これにより、誤設定による SSH ロックアウトを防ぎます。
- **自動更新**: systemd timer により、GeoIP データベースを毎日自動更新します。更新は一時ディレクトリで行われ、成功時のみ `mv` によってアトミックに入れ替えられます。
- **ダウンロード・リトライ**: GeoIP データベースのダウンロードに失敗した場合、最大3回までリトライを行います。
- **CSV 設定**: シンプルな CSV ファイルでポート番号や範囲を管理できます。

## 🎯 ターゲットとスイートスポット

このツールは、以下のような「スイートスポット」に当てはまる環境で最大の効果を発揮します。

### 最適な環境
1.  **Debian / Ubuntu 系のスタンドアロンサーバー**: さくらのVPS、ConoHa、Linode、DigitalOcean など、OSを直接セットアップして管理する「ペット型」のサーバー管理に最適です。
2.  **ホストOS上で直接動くサービス**: コンテナ（Docker等）を使わず、OS上で直接 sshd, nginx, apache2, postfix などのプロセスを実行している環境。
3.  **直接パケットを受け取る構成**: サーバーの前に Cloudflare や AWS CloudFront などの CDN やリバースプロキシが存在せず、送信元IPアドレスが L4（トランスポート層）でそのまま見える環境。
4.  **個人開発者や小規模チーム**: 「サクッと立てたサーバーを、海外からのブルートフォース攻撃やポートスキャンから手っ取り早く守りたい」という実利重視のニーズに最適です。

### 🙅 導入すべきではない環境
- **コンテナ（Docker/Kubernetes）ベースの環境**: UFW の `INPUT` チェーンをバイパスされるため、本スクリプトによる保護は機能しません。
- **Web フロントに CDN を被せている環境**: CDN のエッジサーバーの IP をブロックしてしまい、正当なユーザーまで巻き添えにするリスクがあります。
- **AWS 等のクラウドネイティブな環境**: AWS なら「Security Group」や「AWS WAF」で GeoIP ブロックを行う方が、インフラレベルで効率的かつスマートです。

## 📋 前提条件

`install.sh` は Debian/Ubuntu システムにおいて、以下の依存パッケージを `apt-get` で自動的にインストールしようと試みます：
- `xtables-addons-common`, `libtext-csv-xs-perl`, `libnet-cidr-lite-perl`, `ipset`, `pkg-config`, `ufw`, `curl`, `python3`

## 🚀 インストール

```bash
git clone https://github.com/jassdack/geoipblock.git
cd geoipblock

# オプション A: ドライラン (適用せずにルールを確認)
sudo ./install.sh --dry-run JP ports.csv

# オプション B: CSVファイルから適用
sudo ./install.sh JP ports.csv

# オプション C: コマンドラインから直接ポートを指定して適用
sudo ./install.sh JP 22,80,443

# オプション D: 複数の国を許可する
# 国コードをカンマで区切って指定します
sudo ./install.sh JP,US,TW ports.csv
```

### MaxMind GeoLite2 Country を使用する

既定はDB-IPです。MaxMindの **GeoLite2 Country CSV** を使用するには、MaxMindアカウントを作成し、ダウンロード権限のあるアカウントIDとライセンスキーを用意してください。
[MaxMind公式の取得手順](https://dev.maxmind.com/geoip/updating-databases/)も参照できます。

インストール前、または既存環境の切り替え時に、設定ファイルがまだなければ作成して編集します。

```bash
sudo test -e /etc/geoipblock.conf || sudo install -o root -g root -m 600 geoipblock.conf.sample /etc/geoipblock.conf
sudoedit /etc/geoipblock.conf
```

```bash
GEOIP_SOURCE=maxmind
MAXMIND_ACCOUNT_ID='数字のアカウントID'
MAXMIND_LICENSE_KEY='ライセンスキー'
```

新規導入時は `sudo ./install.sh JP ports.csv` を実行し、下記の確認手順に従ってください。既存環境は後述の「MaxMind対応前のバージョンから更新する」に従ってください。
更新版の導入後、設定だけを変更した場合は `sudo /usr/local/bin/update-geoip.sh` で反映できます。毎日の自動更新も同じ設定を読みます。
DB-IPへ戻す場合は `GEOIP_SOURCE=dbip` に変更します。インストーラーは既存の設定ファイルを上書きしません。

IPv4・IPv6に対応し、地理的な国を示す `geoname_id` で判定します。国が不明な範囲は除外し、ISPの登録国への置き換えは行いません。
CSV ZIPを `xt_geoip_build` 向けの形式へ変換する方式で、`.mmdb` ファイルの直接読み込みには対応していません。
ダウンロード・変換の失敗や空のビルドでは既存DBを保持します。
更新対象はディスク上のDBです。読み込み済みのファイアウォールルールには、`sudo ufw reload` などで再読み込みするまで古い範囲が残る場合があります。

`/etc/geoipblock.conf` はシェルとして読み込まれ、認証情報を含むため、root所有・権限 `600` で管理してください。認証情報はGitに登録しないでください。
アンインストール後もこの設定は残ります。認証情報も削除する場合は手動で削除してください。
データはMaxMindの利用条件に従って使用してください。MaxMind選択時は、[MaxMind](https://www.maxmind.com) が作成したGeoLite2データを使用します。

### 🚨 ワークフローとロールバック
誤った設定によるロックアウトを防ぐため、3分間の確認ウィンドウが設けられています：
1. `./install.sh` を実行します。
2. **新しい** ターミナルウィンドウを開き、サーバーに SSH 接続できるか確認します。
3. 接続に成功したら、元のターミナルで以下のコマンドを実行して設定を確定させます：
   ```bash
   sudo geoipblock-confirm
   ```
4. 3分以内に確定コマンドが実行されなかった場合、GeoIP ルールは自動的に削除され、UFW がリロードされます。

### 📝 CSV 設定例 (`ports.csv`)
フォーマットは `ポート範囲,メモ,ステータス` です。
```csv
22,SSH Access,block
80,HTTP Web,block
443,HTTPS Secure,block
3000:3010,Dev Web Servers,pass
```
*GeoIP フィルタリングを無効にするには、ステータスを `pass`（または `block` 以外）に変更して `install.sh` を再実行してください。*

## 🔄 MaxMind対応前のバージョンから更新する

既にインストール済みの場合は、以下の手順で更新スクリプトと変換ツールを入れ替えます。既存の許可国・ポート・信頼サブネット・ipset・systemd設定を維持できます。アンインストールは不要です。`git pull` だけでは `/usr/local/bin` の実行ファイルは更新されません。

以下は既存の `update-geoip.service` / `update-geoip.timer` を使用している環境向けです。コマンドは同じシェルで順番に実行し、失敗した場合は次に進まず原因を確認してください。

### 1. 対応版のソースを取得する

クローン済みのリポジトリへ移動して実行します。ローカル変更がある場合は先に退避してください。MaxMind対応版が公開されたブランチで実行する必要があります。

```bash
git status --short
git pull --ff-only
test -f maxmind-to-dbip.py && test -f geoipblock.conf.sample
```

### 2. 自動更新を一時停止し、現状をバックアップする

```bash
sudo systemctl stop update-geoip.timer
systemctl is-active update-geoip.service
```

サービスが `active` または `activating` の場合は終了を待ってください。手動実行中の更新も完了させてから進めます。`inactive` で終了コードが非ゼロになるのは通常の状態です。`failed` の場合は `journalctl -u update-geoip.service` で原因を確認してください。

```bash
GEOIP_BACKUP=$(sudo mktemp -d /var/backups/geoipblock-upgrade.XXXXXXXX)
printf 'Backup: %s\n' "$GEOIP_BACKUP"
sudo cp -a /usr/local/bin/update-geoip.sh "$GEOIP_BACKUP/update-geoip.sh"
sudo cp -a /usr/share/xt_geoip "$GEOIP_BACKUP/xt_geoip"
if sudo test -f /etc/geoipblock.conf; then
    sudo cp -a /etc/geoipblock.conf "$GEOIP_BACKUP/geoipblock.conf"
fi
```

表示されたバックアップ先は復旧用に控えてください。初回の対応前バージョンには通常 `/etc/geoipblock.conf` はありません。

### 3. 依存パッケージと更新ファイルを配置する

```bash
sudo apt-get update
sudo apt-get install -y xtables-addons-common libtext-csv-xs-perl libnet-cidr-lite-perl curl python3
sudo install -o root -g root -m 755 update-geoip.sh /usr/local/bin/update-geoip.sh
sudo install -o root -g root -m 644 maxmind-to-dbip.py /usr/local/bin/maxmind-to-dbip.py
if ! sudo test -e /etc/geoipblock.conf; then
    sudo install -o root -g root -m 600 geoipblock.conf.sample /etc/geoipblock.conf
fi
```

既存サービスの実行パスは変わらないため、unitファイルの置き換えや `daemon-reload` は不要です。この手順では `install.sh` を実行しないため、UFWルールの再生成や3分間の自動ロールバックは行われず、`geoipblock-confirm` も不要です。

### 4. データソースを選び、更新を確認する

DB-IPを継続する場合は `GEOIP_SOURCE=dbip` のまま進めます。MaxMindへ切り替える場合は、上記「MaxMind GeoLite2 Country を使用する」に従い `/etc/geoipblock.conf` に `GEOIP_SOURCE=maxmind` と認証情報を設定してください。

```bash
sudo chown root:root /etc/geoipblock.conf
sudo chmod 600 /etc/geoipblock.conf
sudo systemctl start update-geoip.service
sudo systemctl status update-geoip.service --no-pager
sudo journalctl -u update-geoip.service -n 50 --no-pager
```

`systemctl start` が成功し、ログに更新完了が記録されたことを確認します。oneshotサービスなので、成功後の状態が `inactive (dead)` になるのは正常です。`status=0/SUCCESS` も確認できます。失敗した場合は設定を修正して再試行するか、下記の復旧手順を使用してください。

DBを読み込み済みのルールへ反映するには、管理コンソールなどの復旧経路を確保してから実行します。データソースを変更すると、同じIPの判定国が変わる場合があります。

```bash
sudo ufw reload
```

新しいSSH接続で到達性を確認してから、自動更新を再開します。

```bash
sudo systemctl start update-geoip.timer
systemctl list-timers update-geoip.timer --all
```

### 問題があった場合の復旧

停止したタイマーをそのまま放置しないでください。更新処理が終了していることを確認し、同じシェルの `$GEOIP_BACKUP`（別のシェルなら控えたバックアップ先を再設定）を使って戻します。SSH接続できない場合は管理コンソールから実行します。

```bash
sudo systemctl stop update-geoip.timer
sudo test -f "$GEOIP_BACKUP/update-geoip.sh" && sudo test -d "$GEOIP_BACKUP/xt_geoip"
# 上の確認が成功した場合のみ、以下を実行
sudo cp -a "$GEOIP_BACKUP/update-geoip.sh" /usr/local/bin/update-geoip.sh
sudo mv /usr/share/xt_geoip "$GEOIP_BACKUP/xt_geoip.failed"
sudo cp -a "$GEOIP_BACKUP/xt_geoip" /usr/share/xt_geoip
if sudo test -f "$GEOIP_BACKUP/geoipblock.conf"; then
    sudo cp -a "$GEOIP_BACKUP/geoipblock.conf" /etc/geoipblock.conf
fi
sudo ufw reload
sudo systemctl start update-geoip.timer
```

`xt_geoip.failed` が既に存在する場合は別名を選んでください。対応前のスクリプトは今回追加した設定ファイルと変換ツールを使用しません。新しく作成した認証情報を残したくない場合は `/etc/geoipblock.conf` を手動で削除してください。この復旧はスクリプトとDBを戻すもので、aptで更新したパッケージは戻しません。

## ⚙️ 設定のオーバーライド

自動検知されるローカルネットワークを強制的に上書きしたい場合は、環境変数 `TRUSTED_SUBNETS` を渡して実行してください：
```bash
sudo TRUSTED_SUBNETS="10.0.0.0/8 192.168.1.0/24" ./install.sh JP ports.csv
```

### ルールの優先順位に関する注意
GeoIPブロックは、UFWの `before.rules` の最上部に注入されます。Phase 3（ローカルネットワークの許可）は `ACCEPT` ルールを使用するため、ここでマッチしたローカル通信はGeoIP判定やそれ以降のUFWルールをバイパスします。

*   **特定のローカルIPを拒否したい場合**:
    1. `before.rules` 内の `geoipblock` マーカーよりも**上**に拒否ルールを手動で記述する。
    2. または、`TRUSTED_SUBNETS` を使用して、信頼できる特定の管理用IPのみに絞り込む。
*   `TRUSTED_SUBNETS=""`（空文字）を指定してインストールすると、この自動許可フェーズを完全に無効化できます。

## 🛠️ メンテナンスと監視

```bash
# タイマーの状態確認
systemctl status update-geoip.timer

# 更新ログの確認
journalctl -u update-geoip.service
```

## 🖤 手動ブラックリスト (ipset)

防御システムの Phase 1 では、`persistent_offenders` という名前の高速な `ipset` を使用しています。許可された国からのアクセスであっても、特定のIPを個別に30日間ブロックしたい場合に使用できます：

```bash
# 特定のIPをブロック
sudo ipset add persistent_offenders 1.2.3.4

# ブラックリストからIPを削除
sudo ipset del persistent_offenders 1.2.3.4

# ブラックリストの一覧表示
sudo ipset list persistent_offenders
```

## 🤝 Fail2Ban との連携

Fail2Ban と連携させることで、多層防御をより強固にできます。Fail2Ban のアクションとして `persistent_offenders` ipset を指定することで、ブルートフォース攻撃者を最速の Phase 1 で遮断できます。

### Fail2Ban アクション設定例 (`/etc/fail2ban/action.d/geoipblock.conf`)
```ini
[Definition]
actionban = ipset add persistent_offenders <ip> -exist
actionunban = ipset del persistent_offenders <ip> -exist
```

## 🔐 Let's Encrypt (Certbot) との互換性
HTTP-01 認証を使用している場合、更新時に GeoIP ブロックを一時的にバイパスするために以下のフックを使用してください：
```bash
--pre-hook "iptables -I ufw-before-input 1 -p tcp --dport 80 -j ACCEPT; ip6tables -I ufw6-before-input 1 -p tcp --dport 80 -j ACCEPT" \
--post-hook "iptables -D ufw-before-input -p tcp --dport 80 -j ACCEPT; ip6tables -D ufw6-before-input -p tcp --dport 80 -j ACCEPT"
```

## 🧹 アンインストール
```bash
sudo ./uninstall.sh
```

## ⚠️ 既知の制限と互換性 (Known Limitations)

### 1. Docker による UFW のバイパス
Docker はコンテナのポートを公開する際、iptables の `PREROUTING` チェーンと `DOCKER` チェーンを直接操作するため、**UFW の `INPUT` チェーンを完全にバイパス**します。
- `docker run -p 80:80` などで公開されたポートは、本スクリプトの GeoIP ブロックの**保護対象外**となります。

### 2. CDN と リバースプロキシ (Cloudflare など)
本スクリプトは L4 レイヤー (iptables) で動作します。サーバーが Cloudflare などの CDN の背後にある場合、iptables から見える IP は「訪問者の実際のIP」ではなく「CDN のエッジサーバーのIP」になります。
- CDN を利用している場合、`ports.csv` で該当する HTTP/HTTPS ポートを `pass` に設定し、GeoIP の制御は CDN 側の WAF に任せてください。そうしないと、正当なトラフィックであっても CDN エッジの国籍によっては遮断されてしまいます。

## ⚠️ トラブルシューティング
- **データベースのダウンロード失敗**: `/etc/geoipblock.conf` の `GEOIP_SOURCE` を確認してください。MaxMindの場合はアカウントID・ライセンスキー・GeoLite2の取得権限と、MaxMindおよびリダイレクト先へのHTTPS接続を確認します。DB-IPの場合は新しい `xtables-addons` を使用してください。
- **ルールが適用されない**: `lsmod | grep xt_geoip` を実行してカーネルモジュールがロードされているか確認してください。OpenVZ などの VPS カーネルではカスタムモジュールがサポートされていない場合があります。
- **UFW エラー**: `/var/log/syslog` を確認して、iptables の構文エラーをチェックしてください。

## ⚖️ 免責事項
**自己責任で使用してください。** このツールはシステムのファイアウォールルールを変更します。
- このスクリプトの使用によって生じた損害、データの損失、サーバーへのアクセス不能について、作者は一切の責任を負いません。
- 本ツールの使用によるいかなる損害（サーバーへのアクセス不能等）についても、作者は一切の責任を負いません。自己責任でご利用ください。

## 📜 謝辞とデータソース
このツールは `xtables-addons` が提供する `xt_geoip` モジュールに依存しています。
- この製品は、[https://db-ip.com](https://db-ip.com) で公開されている DB-IP IP to City Lite データベースを使用しています。このデータベースは、クリエイティブ・コモンズ表示4.0国際ライセンス（CC-BY 4.0）の下でライセンスされています。
- または、MaxMind が作成した GeoLite2 データ（[https://www.maxmind.com](https://www.maxmind.com) で入手可能）が含まれている場合があります。

## 🔮 将来の展望と技術的負債について

インフラ技術の進化に伴い、本ツールの利用にあたっては以下の長期的なトレンドを考慮する必要があります。

1.  **iptables エコシステムの終焉**: Linux のパケットフィルタリングは `iptables` から `nftables` への移行が進んでいます。本ツールの中核である `xt_geoip` は `xtables-addons` に依存しているため、将来的にディストリビューションから `iptables` サポートが完全に削除された場合、本ツールはその寿命を迎えることになります。
2.  **コンテナオーケストレーション (Kubernetes等) との溝**: モダンなコンテナ環境では、ホストOSの `before.rules` を直接操作する手法は、ネットワークポリシーの管理を複雑にし、Pod 間の通信に予期せぬ影響を与えるリスクがあります。
3.  **「エッジ防御」へのシフト**: Cloudflare や AWS WAF のような CDN/WAF（エッジネットワーク）での GeoIP ブロックが普及しています。「パケットをオリジンサーバーまで到達させてから CPU リソースを使って捨てる」というアプローチは、クラウドのベストプラクティスからは外れつつあります。

*本ツールは「スタンドアロンVPS」という特定の領域において依然として強力で実用的な解決策ですが、新規の長大規模なクラウドネイティブ構成を設計する場合は、各クラウドのネイティブなファイアウォール機能の利用を推奨します。*
