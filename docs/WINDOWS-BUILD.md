# Building a Windows executable (volunteer guide)

This is a simple way to build a portable Windows version of Lekhika on a Windows machine. It produces a folder containing `Lekhika.exe` (not a signed installer).

## Requirements

- Windows 10/11
- Python 3.9 or newer installed from [python.org](https://www.python.org/downloads/)
  - During install, tick **"Add python.exe to PATH"**
- Git (optional, if cloning the repository)

## Steps

1. Get the source code and open PowerShell in the repository root:

   ```powershell
   git clone https://github.com/Vidyut/lekhika.git
   cd lekhika
   ```

   (Or download/unzip the source and `cd` into that folder.)

2. Run the build script:

   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\build_windows_exe.ps1
   ```

3. Test the result:

   ```powershell
   .\dist\Lekhika\Lekhika.exe
   ```

4. To share with others, zip the entire `dist\Lekhika` folder.

## Notes

- The script creates a separate virtual environment at `.venv-build` and installs PyInstaller there.
- If the app fails to start, rebuild with console output for debugging by editing `scripts/build_windows_exe.ps1` and replacing `--windowed` with `--console`, then run again and check the terminal output.
- Antivirus software may flag newly built unsigned executables; this is common for PyInstaller builds.
- For official/release builds, a CI workflow (e.g. GitHub Actions on `windows-latest`) and/or code signing is recommended.
