"""One-time setup script for Outlook OAuth (O365 library).

Uses the O365 library's device code flow — no browser redirect URL copy-paste needed.
After running, a local token file is generated for subsequent use.

Prerequisites:
  1. pip install O365
  2. Go to https://portal.azure.com → App registrations → New registration
     Name: NovelBot
     Supported account types: any org + personal
     Redirect URI: Mobile and desktop → https://login.microsoftonline.com/common/oauth2/nativeclient
  3. Allow public client flows: Yes
  4. API permissions → Add → Microsoft Graph → Delegated → Mail.Send + offline_access
  5. Note your Application (client) ID and create a Client Secret

Usage:
  Fill in client_id and client_secret below, then run: python setup_outlook.py
"""

from O365 import Account

# Fill in your Azure App credentials
client_id = ''
client_secret = ''

credentials = (client_id, client_secret)
account = Account(credentials)
scopes = ['Mail.Send', 'offline_access']

if not account.is_authenticated:
    print("=" * 50)
    print("Starting device code authentication")
    print("Open https://microsoft.com/devicelogin and enter the code shown below")
    print("=" * 50)

    account.authenticate(scopes=scopes)

    if account.is_authenticated:
        print("\nAuthentication successful.")
        print("Token saved locally (o365_token.txt).")
        print("To get the refresh_token for config.yaml:")
        print("  The token is stored in o365_token.txt in this directory.")
