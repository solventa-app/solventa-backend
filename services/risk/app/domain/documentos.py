"""Validadores y generadores de documentos de identidad por país y algoritmos de control."""

import random
import re
import string

_SOLO_DIGITOS = re.compile(r"\D")


def _digitos(valor: str) -> str:
    """Extrae únicamente los dígitos de una cadena."""
    return _SOLO_DIGITOS.sub("", valor)


# ---------------------------------------------------------------- Algoritmo de Luhn (Mod 10)
def validar_luhn(valor: str) -> bool:
    """Valida una cadena numérica usando el algoritmo de Luhn (módulo 10)."""
    d = _digitos(valor)
    if not d:
        return False
    suma = 0
    par = False
    for c in reversed(d):
        n = int(c)
        if par:
            n *= 2
            if n > 9:
                n -= 9
        suma += n
        par = not par
    return suma % 10 == 0


# ---------------------------------------------------------------- Colombia: Cédula de ciudadanía
def validar_cc_co(valor: str) -> str:
    """Valida cédula de ciudadanía colombiana (6 a 10 dígitos, no inicia con 0)."""
    d = _digitos(valor)
    if not 6 <= len(d) <= 10 or d.startswith("0"):
        raise ValueError("La cédula colombiana debe tener entre 6 y 10 dígitos")
    return d


def generar_cc_co(rng: random.Random) -> str:
    """Genera una cédula colombiana aleatoria válida."""
    return str(rng.randint(10_000_000, 1_299_999_999))


# ---------------------------------------------------------------- México: CURP
_CURP = re.compile(r"^[A-Z]{4}\d{6}[HMX][A-Z]{5}[A-Z0-9]\d$")


def validar_curp_mx(valor: str) -> str:
    """Valida CURP mexicana (18 caracteres con formato oficial)."""
    v = valor.strip().upper()
    if not _CURP.match(v):
        raise ValueError("CURP inválida (18 caracteres, formato AAAA000000HXXXXX00)")
    return v


def generar_curp_mx(rng: random.Random) -> str:
    """Genera una CURP mexicana válida para pruebas."""
    letras = string.ascii_uppercase
    fecha = f"{rng.randint(60, 99):02d}{rng.randint(1, 12):02d}{rng.randint(1, 28):02d}"
    return (
        "".join(rng.choices(letras, k=4))
        + fecha
        + rng.choice("HM")
        + "".join(rng.choices(letras, k=5))
        + rng.choice(letras)
        + str(rng.randint(0, 9))
    )


# ------------------------------------------------ Brasil: CPF (con dígitos verificadores)
def _dv_cpf(base: str) -> str:
    for _ in range(2):
        peso = len(base) + 1
        s = sum(int(c) * (peso - i) for i, c in enumerate(base))
        r = (s * 10) % 11
        base += str(0 if r == 10 else r)
    return base[-2:]


def validar_cpf_br(valor: str) -> str:
    """Valida CPF brasileño calculando sus dos dígitos verificadores."""
    d = _digitos(valor)
    if len(d) != 11 or d == d[0] * 11 or _dv_cpf(d[:9]) != d[9:]:
        raise ValueError("CPF inválido")
    return d


def generar_cpf_br(rng: random.Random) -> str:
    """Genera un CPF brasileño válido con sus dígitos verificadores."""
    base = "".join(str(rng.randint(0, 9)) for _ in range(9))
    # Evitar secuencias homogéneas que fallan la validación
    if base == base[0] * 9:
        base = "123456789"
    return base + _dv_cpf(base)


# ---------------------------------------------------------------- Chile: RUT (módulo 11)
def _dv_rut(cuerpo: str) -> str:
    s, m = 0, 2
    for c in reversed(cuerpo):
        s += int(c) * m
        m = 2 if m == 7 else m + 1
    r = 11 - (s % 11)
    return {11: "0", 10: "K"}.get(r, str(r))


def validar_rut_cl(valor: str) -> str:
    """Valida RUT chileno mediante algoritmo de módulo 11."""
    v = valor.replace(".", "").replace("-", "").strip().upper()
    if not re.fullmatch(r"\d{7,8}[0-9K]", v) or _dv_rut(v[:-1]) != v[-1]:
        raise ValueError("RUT inválido")
    return f"{v[:-1]}-{v[-1]}"


def generar_rut_cl(rng: random.Random) -> str:
    """Genera un RUT chileno válido formateado con guion."""
    cuerpo = str(rng.randint(5_000_000, 25_999_999))
    return f"{cuerpo}-{_dv_rut(cuerpo)}"


# ---------------------------------------------------------------- Perú: DNI
def validar_dni_pe(valor: str) -> str:
    """Valida DNI peruano (8 dígitos numéricos)."""
    d = _digitos(valor)
    if len(d) != 8:
        raise ValueError("El DNI peruano debe tener 8 dígitos")
    return d


def generar_dni_pe(rng: random.Random) -> str:
    """Genera un DNI peruano válido."""
    return f"{rng.randint(10_000_000, 79_999_999)}"


# ---------------------------------------------------------------- Estados Unidos: SSN
def validar_ssn_us(valor: str) -> str:
    """Valida SSN estadounidense (9 dígitos, respetando reglas de áreas y grupos prohibidos)."""
    d = _digitos(valor)
    if len(d) != 9:
        raise ValueError("El SSN debe tener 9 dígitos")
    area, grupo, serie = d[:3], d[3:5], d[5:]
    if area in ("000", "666") or area.startswith("9") or grupo == "00" or serie == "0000":
        raise ValueError("SSN inválido")
    return d


def generar_ssn_us(rng: random.Random) -> str:
    """Genera un SSN estadounidense válido."""
    area = rng.choice([a for a in range(1, 900) if a != 666])
    return f"{area:03d}{rng.randint(1, 99):02d}{rng.randint(1, 9999):04d}"


# ------------------------------------------------ España: DNI / NIE (letra de control)
_LETRAS_DNI = "TRWAGMYFPDXBNJZSQVHLCKE"


def validar_dni_es(valor: str) -> str:
    """Valida DNI o NIE español comprobando su letra de control con módulo 23."""
    v = valor.replace("-", "").replace(" ", "").upper()
    m = re.fullmatch(r"([XYZ]?)(\d{7,8})([A-Z])", v)
    if not m:
        raise ValueError("DNI/NIE inválido")
    prefijo, numero, letra = m.groups()
    n = int(str("XYZ".index(prefijo)) + numero) if prefijo else int(numero)
    if _LETRAS_DNI[n % 23] != letra:
        raise ValueError("Letra de control del DNI/NIE incorrecta")
    return v


def generar_dni_es(rng: random.Random) -> str:
    """Genera un DNI español válido con su letra de control."""
    n = rng.randint(10_000_000, 99_999_999)
    return f"{n}{_LETRAS_DNI[n % 23]}"


# ---------------------------------------------------------------- Alemania: Steuer-ID
def validar_steuerid_de(valor: str) -> str:
    """Valida Steuer-ID alemana (11 dígitos, no empieza en 0)."""
    d = _digitos(valor)
    if len(d) != 11 or d.startswith("0"):
        raise ValueError("La Steuer-ID debe tener 11 dígitos y no empezar por 0")
    return d


def generar_steuerid_de(rng: random.Random) -> str:
    return str(rng.randint(10_000_000_000, 99_999_999_999))


# ---------------------------------------------------------------- Reino Unido: NINO
_NINO = re.compile(r"^(?!BG|GB|NK|KN|TN|NT|ZZ)[A-CEGHJ-PR-TW-Z][A-CEGHJ-NPR-TW-Z]\d{6}[A-D]$")


def validar_nino_gb(valor: str) -> str:
    """Valida National Insurance Number británico."""
    v = valor.replace(" ", "").upper()
    if not _NINO.match(v):
        raise ValueError("National Insurance Number inválido")
    return v


def generar_nino_gb(rng: random.Random) -> str:
    return "AB" + f"{rng.randint(0, 999_999):06d}" + rng.choice("ABCD")


# ---------------------------------------------------------------- China: Resident Identity Card
_PESOS_CN = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
_CONTROL_CN = "10X98765432"


def _dv_cn(base17: str) -> str:
    return _CONTROL_CN[sum(int(c) * p for c, p in zip(base17, _PESOS_CN, strict=True)) % 11]


def validar_rid_cn(valor: str) -> str:
    """Valida cédula de identidad china (ISO 7064 MOD 11-2)."""
    v = valor.strip().upper()
    if not re.fullmatch(r"\d{17}[\dX]", v) or _dv_cn(v[:17]) != v[17]:
        raise ValueError("Número de identidad chino inválido")
    return v


def generar_rid_cn(rng: random.Random) -> str:
    base = (
        f"{rng.choice([110101, 310101, 440301, 510104])}"
        f"{rng.randint(1960, 2004)}{rng.randint(1, 12):02d}{rng.randint(1, 28):02d}"
        f"{rng.randint(0, 999):03d}"
    )
    return base + _dv_cn(base)


def enmascarar(documento: str) -> str:
    """Enmascara un documento mostrando solo los últimos 4 caracteres."""
    visibles = documento[-4:]
    return "*" * max(len(documento) - 4, 2) + visibles
