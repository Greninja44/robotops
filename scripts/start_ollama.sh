#!/usr/bin/env bash
# Make sure an Ollama server answers on localhost:11434.
# On this machine Ollama is installed on the Windows host; WSL mirrored networking exposes it on localhost.
URL="${ROBOTOPS_OLLAMA_URL:-http://127.0.0.1:11434}"
up() { curl -sf -m 2 "$URL/api/tags" >/dev/null; }
if up; then echo "ollama already reachable"; exit 0; fi
if command -v ollama >/dev/null 2>&1; then
  nohup ollama serve >"$(dirname "$0")/../logs/ollama.log" 2>&1 &
else
  EXE="$(ls /mnt/c/Users/*/AppData/Local/Programs/Ollama/ollama.exe 2>/dev/null | head -1)"
  [[ -n "$EXE" ]] && { nohup "$EXE" serve >"$(dirname "$0")/../logs/ollama.log" 2>&1 & } || { echo "ollama not found" >&2; exit 1; }
fi
for _ in $(seq 1 30); do up && { echo "ollama started"; exit 0; }; sleep 1; done
echo "ollama did not come up" >&2; exit 1
