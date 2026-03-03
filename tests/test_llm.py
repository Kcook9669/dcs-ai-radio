import requests

response = requests.post("http://localhost:11434/api/generate", json={
    "model": "llama3.1",
    "prompt": "You are Batumi ATC. A pilot requests landing. Respond in one brief radio call.",
    "stream": False
})

print(response.json())