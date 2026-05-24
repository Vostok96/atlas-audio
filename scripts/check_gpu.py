"""
check_gpu.py — Primer hito. Verifica que PyTorch ve tu RTX 5070 Ti (Blackwell, sm_120)
y que NO aparece el warning de incompatibilidad.

Ejecuta:  python scripts/check_gpu.py

Resultado esperado:
  - CUDA disponible: True
  - GPU: NVIDIA GeForce RTX 5070 Ti
  - Compute capability: (12, 0)   <- sm_120
  - Una pequeña operación en GPU se ejecuta sin error.

Si ves 'no kernel image is available' o un warning de sm_120, NO tienes la build
correcta. Reinstala con:
  pip uninstall torch torchvision torchaudio -y
  pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128
"""

import sys


def main() -> int:
    try:
        import torch
    except ImportError:
        print("[X] PyTorch no está instalado.")
        print("    pip install torch torchvision torchaudio "
              "--index-url https://download.pytorch.org/whl/cu128")
        return 1

    print(f"PyTorch version : {torch.__version__}")
    print(f"CUDA disponible : {torch.cuda.is_available()}")

    if not torch.cuda.is_available():
        print("[X] CUDA no disponible. Revisa el driver de NVIDIA y la build cu128.")
        return 1

    print(f"CUDA (torch)    : {torch.version.cuda}")
    name = torch.cuda.get_device_name(0)
    cap = torch.cuda.get_device_capability(0)
    print(f"GPU             : {name}")
    print(f"Compute cap.    : sm_{cap[0]}{cap[1]}  {cap}")

    if cap[0] < 12:
        print("[!] Aviso: no se detecta Blackwell (sm_120). Si tu tarjeta es RTX 50xx,")
        print("    probablemente la build de torch no es la de cu128.")

    # Prueba real: una operación en GPU. Aquí es donde explota si faltan los kernels sm_120.
    try:
        x = torch.randn(2048, 2048, device="cuda")
        y = torch.mm(x, x)
        torch.cuda.synchronize()
        print(f"[OK] Operación en GPU correcta. Suma de control: {float(y.sum()):.2f}")
    except Exception as e:  # noqa: BLE001
        print("[X] Falló la operación en GPU (kernels sm_120 ausentes):")
        print(f"    {e}")
        print("    Reinstala torch con el índice cu128 (ver cabecera de este archivo).")
        return 1

    free, total = torch.cuda.mem_get_info()
    print(f"VRAM libre/total: {free/1e9:.1f} GB / {total/1e9:.1f} GB")
    print("\n[OK] Todo correcto. Tu GPU está lista para XTTSv2 y faster-whisper.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
