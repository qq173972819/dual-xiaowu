export class XiaoWuClient {
  constructor(private baseUrl: string, private appToken: string) {}
  private async request(path: string, init: RequestInit = {}) {
    const response = await fetch(`${this.baseUrl.replace(/\/$/, "")}${path}`, {
      ...init,
      headers: { Authorization: `Bearer ${this.appToken}`, "Content-Type": "application/json", ...init.headers },
    });
    if (!response.ok) throw new Error(`XiaoWu API HTTP ${response.status}`);
    return response.status === 204 ? undefined : response.json();
  }
  pushContent(deviceId: string, template: string, data: Record<string, unknown>, screen = "A") {
    return this.request(`/openapi/v1/devices/${deviceId}/content`, {
      method: "POST", body: JSON.stringify({ template, screen, data, expires_in_seconds: 3600 }),
    });
  }
  device(deviceId: string) { return this.request(`/openapi/v1/devices/${deviceId}`); }
  events(deviceId: string) { return this.request(`/openapi/v1/devices/${deviceId}/events`); }
}
