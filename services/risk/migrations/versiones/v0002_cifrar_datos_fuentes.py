"""Alias compatible de `0003_cifrar_datos_fuentes` para `v0002_cifrar_datos_fuentes`."""

import importlib

_modulo = importlib.import_module("migrations.versiones.0003_cifrar_datos_fuentes")
aplicar = _modulo.aplicar

__all__ = ["aplicar"]
