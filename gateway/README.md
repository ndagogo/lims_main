# HFDN Local Analyzer Gateway — File-drop Adapter

This is a **prototype adapter** for analyzer middleware that exports completed messages as HL7 text files. It is suitable for controlled integration testing, not direct clinical production use.

## Configure

1. Create an API service account with access only to the relevant centre and analyzer integration functions.
2. Configure analyzer middleware to export completed messages to a local folder such as `C:\HFDN-Gateway\inbox`.
3. Set these environment variables in the Windows service account environment:
   - `HFDN_API_BASE` — full HTTPS API base ending in `/api` (example `https://lis.example.org/api`)
   - `HFDN_API_TOKEN` — short-lived bearer access token; do not put it in source control
   - `HFDN_ANALYZER_ID` — analyzer UUID configured in the platform
   - `HFDN_INBOX_DIR`, `HFDN_PROCESSED_DIR`, `HFDN_FAILED_DIR` — local folders
4. Run `python file_drop_agent.py` as a dedicated non-administrator service account.
5. Confirm messages appear in `/api/analyzer-messages/`, then parse them and manually map/review results.

## Important limitations

- The platform endpoint currently expects an authenticated Django user token. For deployment, use a dedicated service account, secure token rotation, HTTPS, network restrictions and monitoring. Do not use a staff member's personal token.
- The file-drop adapter does not implement ASTM framing, serial/TCP listeners, instrument acknowledgements, vendor-specific HL7 profiles or automatic sample matching.
- Failed files are moved to the failed folder for operator review; define an approved retry/replay procedure to avoid duplicate messages.
- Messages may contain patient identifiers. Restrict folder permissions, encrypt disks, set retention rules, and never email raw messages.
- Never enable automatic result release from an analyzer message. Keep result review/authentication/release inside the LIMS workflow.
