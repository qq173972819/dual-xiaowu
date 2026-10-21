"""Zero-dependency XiaoWu OpenAPI client example."""
import json
from urllib.request import Request, urlopen


class XiaoWuClient:
    def __init__(self, base_url: str, app_token: str):
        self.base_url = base_url.rstrip("/")
        self.app_token = app_token

    def _request(self, method: str, path: str, body=None):
        data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        request = Request(self.base_url + path, data=data, method=method, headers={
            "Authorization": f"Bearer {self.app_token}", "Content-Type": "application/json"
        })
        with urlopen(request, timeout=20) as response:
            raw = response.read()
            return json.loads(raw) if raw else None

    def push_content(self, device_id: str, template: str, data: dict, screen: str = "A"):
        return self._request("POST", f"/openapi/v1/devices/{device_id}/content", {
            "template": template, "screen": screen, "data": data, "expires_in_seconds": 3600
        })

    def device(self, device_id: str):
        return self._request("GET", f"/openapi/v1/devices/{device_id}")

    def events(self, device_id: str):
        return self._request("GET", f"/openapi/v1/devices/{device_id}/events")
