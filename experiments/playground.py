from array import array
from datetime import date, datetime, timezone

# --- Integers (type code "i" = signed int; pick "l", "q", etc. for wider ranges) ---
numbers = array("i", [1, 2, 3])
# Only values matching the type code are allowed (here: integers).

# --- Floats: "f" = C float, "d" = C double (usual choice for decimals) ---
floats = array("f", [1.0, 2.5, 3.14])
more_precise = array("d", [1.0, 2.5, 3.141592653589793])

# --- Strings: array is for homogeneous numeric/binary buffers, not Python str ---
# Use a list when you need a sequence of strings:
words: list[str] = ["hello", "array", "module"]
# Use bytes/bytearray for raw byte buffers (often what you want for I/O):
raw = bytearray(b"hello")

# --- Dates: no array type for datetime/date; keep them in a list ---
events: list[date] = [date(2026, 4, 1), date(2026, 4, 16)]
moments: list[datetime] = [
    datetime(2026, 4, 16, 12, 0, tzinfo=timezone.utc),
]
# If you need compact numeric storage, store Unix timestamps instead:
timestamps = array("d", [m.timestamp() for m in moments])

if __name__ == "__main__":
    print(numbers, floats, more_precise)
    print(words, raw)
    print(events, moments, list(timestamps))
