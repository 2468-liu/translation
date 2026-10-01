# Third-party notices

This project is a local integration and does not bundle the downloaded model weights or the FFmpeg build in Git.

## faster-whisper-small

The local CTranslate2 model is downloaded from the `Systran/faster-whisper-small` model repository. Its model card states the MIT license. Keep the model repository's license and attribution with any separately shared model copy.

Source: https://huggingface.co/Systran/faster-whisper-small

## Hy-MT2

The local translation weights are the `Tencent/Hy-MT2-1.8B-GGUF` model. The upstream model README and license terms apply to those weights. This repository does not relicense or claim ownership of the model.

Source: https://huggingface.co/tencent/Hy-MT2-1.8B-GGUF

## FFmpeg

The optional local FFmpeg build is kept outside Git under `tools/ffmpeg/`. Its distribution includes its own `LICENSE` and `README.txt`; consult those files before sharing the binary. Do not remove or replace its license notices.

## Fixture media

`fixtures/SOURCE.txt` records the attribution for the Creative Commons test video. The file is used only for local ASR and subtitle verification and remains under the stated CC BY 4.0 attribution terms.

## Project code

No third-party project source or watermark was identified in the extension or server code during the license review. This notice does not grant rights beyond the upstream terms above.
