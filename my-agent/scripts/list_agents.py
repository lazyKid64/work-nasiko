"""List all agents in the Nasiko registry."""
import requests
import json

r = requests.get("http://localhost:8000/api/v1/registry", timeout=10)
agents = r.json()

print(f"{'ID':<20} {'Name':<35} {'URL'}")
print("-" * 90)
for a in agents:
    name = a.get("name", "?")
    aid = a.get("id", "?")
    url = a.get("url", "?")
    print(f"{aid:<20} {name:<35} {url}")
