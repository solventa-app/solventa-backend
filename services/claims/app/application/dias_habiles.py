"""Plazos en días hábiles (W04-7): la misma regla que usaba el dummy de siniestros."""

from datetime import datetime, timedelta, timezone

HORA_COLOMBIA = timezone(timedelta(hours=-5))  # Colombia no tiene horario de verano
HORA_VENCIMIENTO = 17


def sumar_dias_habiles(desde: datetime, dias: int) -> datetime:
    """Suma días hábiles (lunes a viernes) y fija el vencimiento a las 5:00 p. m. hora Colombia.

    No descuenta festivos: falta un calendario oficial (anotado en el README)."""
    fecha = desde.astimezone(HORA_COLOMBIA)
    restantes = dias
    while restantes > 0:
        fecha += timedelta(days=1)
        if fecha.weekday() < 5:
            restantes -= 1
    return fecha.replace(hour=HORA_VENCIMIENTO, minute=0, second=0, microsecond=0)
