import argparse
import json
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def request(method: str, url: str, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = Request(url, data=data, method=method, headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urlopen(req, timeout=10) as res:
            raw = res.read()
            return res.status, json.loads(raw) if raw else None
    except HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def main():
    parser = argparse.ArgumentParser(description="XiaoWu S3 device simulator")
    parser.add_argument("--server", default="http://127.0.0.1:8100")
    parser.add_argument("--device-id", default="SIM-S3-001")
    parser.add_argument("--token")
    parser.add_argument("--interval", type=int, default=5)
    args = parser.parse_args()
    base = args.server.rstrip("/")
    token = args.token
    if not token:
        code, result = request("POST", f"{base}/api/v1/devices/register", {"device_id": args.device_id, "firmware_version": "simulator-0.1"})
        if code == 409:
            raise SystemExit("设备已注册；请用 --token 传入首次注册返回的 token")
        if code != 201:
            raise SystemExit(f"注册失败: {code} {result}")
        token = result["device_token"]
        print(f"已注册 {args.device_id}，请保存 token: {token}")
    headers = {"X-Device-Token": token}
    while True:
        request("POST", f"{base}/api/v1/devices/{args.device_id}/heartbeat", {"firmware_version": "simulator-0.1", "battery_voltage": 4.0, "rssi": -48, "runtime_mode": "active"}, headers)
        code, task = request("GET", f"{base}/api/v1/devices/{args.device_id}/tasks/next", headers=headers)
        if code == 200 and task:
            payload = task["payload"]
            print(f"屏幕 {payload['screen']} 显示 [{payload['template']}]: {json.dumps(payload['data'], ensure_ascii=False)}")
            request("POST", f"{base}/api/v1/devices/{args.device_id}/tasks/{task['id']}/ack", {"status": "displayed"}, headers)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
