"""Print whether candidate ModelScope repos exist and which weight files they have."""

from modelscope.hub.api import HubApi

CANDIDATES = [
    "Qwen/Qwen2.5-3B-Instruct-GGUF",
    "qwen/Qwen2.5-3B-Instruct-GGUF",
    "AI-ModelScope/whisper-small",
    "AI-ModelScope/whisper-base",
    "pengzhendong/faster-whisper-small",
    "pengzhendong/faster-whisper-base",
    "keepitsimple/faster-whisper-small",
    "iic/speech_paraformer-large-vad-punc_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
]


def walk(nodes, names):
    if isinstance(nodes, dict):
        nodes = nodes.get("Files") or nodes.get("files") or nodes.get("Data") or [nodes]
    if not isinstance(nodes, list):
        return
    for node in nodes:
        if not isinstance(node, dict):
            continue
        name = node.get("Name") or node.get("Path") or node.get("name") or ""
        if name:
            names.append(name)
        kids = node.get("Children") or node.get("children") or node.get("Files")
        if kids:
            walk(kids, names)


def main():
    api = HubApi()
    for model_id in CANDIDATES:
        try:
            files = api.get_model_files(model_id, recursive=True)
            names = []
            walk(files, names)
            interesting = [
                name
                for name in names
                if name.endswith((".gguf", ".bin", ".onnx", ".safetensors", ".pt", ".json"))
                or "q4" in name.lower()
            ]
            print(f"OK {model_id} files={len(names)}")
            for name in interesting[:20]:
                print(f"   {name}")
        except Exception as exc:
            print(f"FAIL {model_id} {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
