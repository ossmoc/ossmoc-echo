"""Known game player-name tokens; replacement never mutates source records."""
import re
import unicodedata


PLAYER_NAME_PATTERN = re.compile(
    r"\{player[-_]?name\}|\{Cus:Var,\s*VarType=Global\s+Key=main_team_name\}",
    re.IGNORECASE,
)


def normalize_player_name(value) -> str:
    if not isinstance(value, str):
        return ""
    value = unicodedata.normalize('NFC', value)
    # Single-line names, with no invisible control/bidi characters.
    value = ''.join(' ' if char.isspace() else char for char in value
                    if char.isspace() or not unicodedata.category(char).startswith('C'))
    return ' '.join(value.split())[:40].strip()


def replace_player_name(text: str, name: str) -> str:
    # A callable keeps backslashes literal and avoids re-scanning inserted text.
    return PLAYER_NAME_PATTERN.sub(lambda _: name, text)
