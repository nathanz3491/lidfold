from lidfold.install import SHIM, with_entry, without_entry

BIN = r"C:\Users\me\AppData\Local\Programs\lidfold\bin"


def test_path_entry_added_once():
    value = r"C:\Windows;C:\Tools"
    once = with_entry(value, BIN)
    assert once == value + ";" + BIN
    assert with_entry(once, BIN) == once
    assert with_entry(once, BIN.upper() + "\\") == once        # case and trailing slash


def test_path_entry_removed_without_touching_others():
    value = r"C:\Windows;" + BIN + r";C:\Tools;"
    assert without_entry(value, BIN) == r"C:\Windows;C:\Tools"
    assert without_entry(r"C:\Windows", BIN) == r"C:\Windows"


def test_cli_shim_points_at_the_app_venv_relative_to_itself():
    assert SHIM.startswith(r'@"%~dp0..\venv\Scripts\lidfold.exe" %*')
