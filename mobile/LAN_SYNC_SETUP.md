# ASTA Mobile LAN Sync

ASTA Mobile stores changes on the phone first, then retries task sync when the laptop backend is reachable. The app retries on launch, when it returns to the foreground, every 30 seconds while open, and immediately after a task change. Both devices must be on the same trusted Wi-Fi; the laptop must be awake and the backend running.

## One-time laptop setup

1. Open PowerShell **as Administrator** on the trusted home Wi-Fi and run:
   ```powershell
   Set-ExecutionPolicy -Scope Process Bypass
   $trustedWifi = (Get-NetConnectionProfile -InterfaceAlias 'Wi-Fi').Name
   & 'C:\Asta\scripts\setup_asta_mobile_lan.ps1' -TrustedWifiName $trustedWifi
   ```
   This marks the confirmed Wi-Fi as Private and allows only TCP 8443 from the local subnet.
2. Start the backend in a regular PowerShell window:
   ```powershell
   & 'C:\Asta\scripts\start_asta_mobile_sync.ps1'
   ```
   Keep this window open while you want mobile sync. It prints the laptop HTTPS address.
3. Transfer `C:\Asta\mobile\asta-home-ca.crt` to Android. On Android open **Settings → Security & privacy → More security settings → Encryption & credentials → Install a certificate → CA certificate** and install it. Android may ask you to set a screen lock first.
4. Install a build of ASTA Mobile that includes the Android network-security config, open **Settings**, set the printed `https://<laptop-ip>:8443` URL, then sign in.

## Using it

Create tasks when the laptop is offline; they stay in the phone's SQLite database and queue. When the laptop server starts, keep ASTA Mobile open or reopen it. It retries automatically and shows the pending count until the backend accepts every change. Notes and focus sessions remain phone-only in this version.

Do not use this endpoint on public Wi-Fi or configure router port forwarding. The CA private key is stored in the ignored `C:\Asta\certs` folder; only `asta-home-ca.crt` is shared with the phone.
