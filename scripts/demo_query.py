import json
from urllib import request


API_URL = "http://127.0.0.1:8000/api/query"


def main() -> None:
    payload = {
        "question": "发动机无法启动怎么办",
        "device_name": "摩托车发动机",
    }
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        API_URL,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=10) as response:
        print(response.read().decode("utf-8"))


if __name__ == "__main__":
    main()
