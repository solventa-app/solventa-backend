import sys
from pathlib import Path

# Las pruebas importan `fakes` por nombre; la carpeta de pruebas debe estar en el path.
sys.path.insert(0, str(Path(__file__).parent))
