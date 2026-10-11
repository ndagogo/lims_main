
## Local gateway prototype
The `gateway/` folder includes a Windows-friendly file-drop bridge for instruments/middleware that can export HL7 text files. It submits messages to the authenticated API and separates processed/failed files. It is a prototype: no ASTM or direct TCP/serial device protocol is included. Test using synthetic messages and a dedicated integration service account before connecting any real analyzer.
