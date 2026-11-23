"""Clima pela Open-Meteo (gratuita, sem chave). Só a cidade vai para a internet; nada mais."""
import time

import requests

_WMO = {
    0: "o céu está limpo", 1: "o céu está quase limpo", 2: "o céu está parcialmente nublado", 3: "o tempo está nublado",
    45: "há neblina", 48: "há neblina", 51: "há garoa fraca", 53: "há garoa", 55: "há garoa forte",
    56: "há garoa congelante", 57: "há garoa congelante", 61: "está chovendo fraco", 63: "está chovendo",
    65: "está chovendo forte", 66: "há chuva congelante", 67: "há chuva congelante", 71: "está nevando fraco",
    73: "está nevando", 75: "está nevando forte", 77: "há grãos de neve", 80: "há pancadas de chuva fracas",
    81: "há pancadas de chuva", 82: "há pancadas de chuva fortes", 85: "há pancadas de neve",
    86: "há pancadas de neve", 95: "há trovoadas", 96: "há trovoadas com granizo", 99: "há trovoadas com granizo forte",
}
_cache = {}


def _local(cidade, pais):
    r = requests.get("https://geocoding-api.open-meteo.com/v1/search",
                     params={"name": cidade, "count": 1, "language": "pt", "countryCode": pais}, timeout=8)
    r.raise_for_status()
    res = (r.json().get("results") or [None])[0]
    if not res:
        raise ValueError(f"cidade não encontrada: {cidade}")
    return res["latitude"], res["longitude"], res["name"]


def dados(cidade, pais="BR"):
    """Dicionário com o clima de agora e a previsão de hoje (cache de 30 min)."""
    chave = (cidade.lower(), pais)
    if chave in _cache and time.time() - _cache[chave][0] < 1800:
        return _cache[chave][1]
    lat, lon, nome = _local(cidade, pais)
    r = requests.get("https://api.open-meteo.com/v1/forecast", params={
        "latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": 1,
        "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,precipitation",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code",
    }, timeout=8)
    r.raise_for_status()
    j = r.json()
    c, d = j["current"], j["daily"]
    out = {
        "cidade": nome,
        "agora_temp": round(c["temperature_2m"]),
        "sensacao": round(c["apparent_temperature"]),
        "agora": _WMO.get(c["weather_code"], "tempo indefinido"),
        "vento_kmh": round(c["wind_speed_10m"]),
        "max": round(d["temperature_2m_max"][0]),
        "min": round(d["temperature_2m_min"][0]),
        "chuva_pct": d["precipitation_probability_max"][0] or 0,
        "hoje": _WMO.get(d["weather_code"][0], ""),
    }
    _cache[chave] = (time.time(), out)
    return out


def resumo(cidade, pais="BR"):
    """Frase curta, pronta para falar."""
    d = dados(cidade, pais)
    s = f"Em {d['cidade']} agora faz {d['agora_temp']} graus e {d['agora']}. "
    s += f"Hoje a mínima é de {d['min']} e a máxima de {d['max']} graus"
    if d["chuva_pct"] >= 30:
        s += f", com {d['chuva_pct']} por cento de chance de chuva"
    return s + "."
