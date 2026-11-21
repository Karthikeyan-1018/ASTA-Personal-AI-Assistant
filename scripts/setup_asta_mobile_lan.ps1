param([Parameter(Mandatory=$true)][string]$TrustedWifiName)

# Run once from PowerShell opened with Run as administrator.
$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Administrator rights are required. Close this window, search for PowerShell, choose Run as administrator, then run this script again.'
}

$profile = Get-NetConnectionProfile -InterfaceAlias 'Wi-Fi' -ErrorAction Stop
if (-not $profile) { throw 'Wi-Fi network profile was not found.' }
if ($profile.Name -ne $TrustedWifiName) {
    throw "Refusing to change firewall settings: connected Wi-Fi '$($profile.Name)' does not match the trusted Wi-Fi you specified '$TrustedWifiName'."
}

switch ($profile.NetworkCategory.ToString()) {
    'Private' { $firewallProfile = 'Private' }
    'DomainAuthenticated' {
        # DomainAuthenticated is controlled by Windows/domain policy; leave it unchanged.
        $firewallProfile = 'Domain'
        Write-Host 'Wi-Fi is DomainAuthenticated; leaving its profile unchanged and scoping the rule to Domain + LocalSubnet.'
    }
    'Public' {
        try {
            Set-NetConnectionProfile -InterfaceAlias 'Wi-Fi' -NetworkCategory Private -ErrorAction Stop
        } catch {
            throw "Windows policy prevented changing this Wi-Fi to Private. Ask your administrator to allow a TCP 8443 inbound rule for the current Wi-Fi profile, restricted to LocalSubnet. Details: $($_.Exception.Message)"
        }
        $profile = Get-NetConnectionProfile -InterfaceAlias 'Wi-Fi' -ErrorAction Stop
        if ($profile.NetworkCategory -ne 'Private') { throw 'Wi-Fi profile did not change to Private; no firewall rule was created.' }
        $firewallProfile = 'Private'
    }
    default { throw "Unsupported Wi-Fi category '$($profile.NetworkCategory)'; no firewall rule was created." }
}

$ruleName = 'ASTA Mobile Sync HTTPS (Private LAN)'
$rule = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
if ($rule) {
    Set-NetFirewallRule -DisplayName $ruleName -Enabled True -Profile $firewallProfile -ErrorAction Stop
    Get-NetFirewallRule -DisplayName $ruleName | Get-NetFirewallAddressFilter | Set-NetFirewallAddressFilter -RemoteAddress LocalSubnet -ErrorAction Stop
} else {
    New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8443 -Profile $firewallProfile -RemoteAddress LocalSubnet -ErrorAction Stop | Out-Null
}
$verified = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction Stop
if (-not $verified.Enabled -or $verified.Action -ne 'Allow') { throw 'Windows did not enable the ASTA HTTPS firewall rule.' }
Write-Host "ASTA mobile sync firewall rule is enabled for $firewallProfile networks, limited to TCP 8443 from LocalSubnet."
