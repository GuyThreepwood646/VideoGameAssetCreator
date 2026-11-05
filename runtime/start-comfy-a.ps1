# Start ComfyUI instance A (3D models) on the RTX 3060, port 8188, when ComfyUI Desktop is not running it.
# Same install as GameDevComfy, same model paths, separate user and output folders on F:.
# The backend runs this on demand (backend/vgac/instances.py) and only ever stops the copy it started.
$env:SKINTOKEN_BLENDER_BIN = "F:\Tools\blender-5.2.3-windows-x64\blender.exe"
Set-Location "C:\Users\evanr\ComfyUI-Installs\ComfyUI (1)"
& ".\ComfyUI\.venv\Scripts\python.exe" -s ComfyUI\main.py `
    --port 8188 --listen 127.0.0.1 --cuda-device 1 `
    --extra-model-paths-config "C:\Users\evanr\AppData\Roaming\Comfy Desktop\instance-model-paths\inst-1790285903050.yaml" `
    --input-directory "C:\Projects\Comfy\input" `
    --user-directory "F:\Projects\VideoGameAssetCreator\runtime\comfy-a-user" `
    --output-directory "F:\Projects\VideoGameAssetCreator\runtime\comfy-a-output"
