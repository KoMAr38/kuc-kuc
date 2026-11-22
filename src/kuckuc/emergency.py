"""Which emergency number to name to a person, by the country of their phone, not by their language:
a Serbian speaker living in Germany is told 112."""

from __future__ import annotations

import phonenumbers

# Country calling code -> the number people there know. Serbia: 194, the ambulance. The EU, the EEA,
# Switzerland and the other European countries listed here: 112.
_BY_COUNTRY_CODE = {
    381: "194",
    1: "911",
    44: "999",
    61: "000",
    **dict.fromkeys(
        (30, 31, 32, 33, 34, 351, 352, 353, 354, 356, 357, 358, 359, 36, 370, 371, 372, 385, 386, 39, 40, 41,
         420, 421, 423, 43, 45, 46, 47, 48, 49),
        "112",
    ),
}  # fmt: skip


def emergency_number(phone: str) -> str | None:
    """None for a phone we cannot read, or a country we know no number for: then plain words are said."""
    try:
        country = phonenumbers.parse(phone).country_code
    except phonenumbers.NumberParseException:
        return None
    return _BY_COUNTRY_CODE.get(country)
