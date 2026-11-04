# Start ComfyUI instance B (images and rigging) on the RTX 3080, port 8189.
# Same install as GameDevComfy, same model paths, separate user and output folders on F:.
$env:SKINTOKEN_BLENDER_BIN = "F:\Tools\blender-5.2.3-windows-x64\blender.exe"
Set-Location "C:\Users\evanr\ComfyUI-Installs\ComfyUI (1)"
& ".\ComfyUI\.venv\Scripts\python.exe" -s ComfyUI\main.py `
    --port 8189 --listen 127.0.0.1 --cuda-device 0 `
    --extra-model-paths-config "C:\Users\evanr\AppData\Roaming\Comfy Desktop\instance-model-paths\inst-1790285903050.yaml" `
    --input-directory "C:\Projects\Comfy\input" `
    --user-directory "F:\Projects\VideoGameAssetCreator\runtime\comfy-b-user" `
    --output-directory "F:\Projects\VideoGameAssetCreator\runtime\comfy-b-output"
