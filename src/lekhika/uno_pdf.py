"""LibreOffice UNO worker for 1.6's PDF route — run as a subprocess.

Plain ``soffice --convert-to pdf`` never refreshes the document: the
DOCX TOC field ships with its cached placeholder ("Update this field…")
because LibreOffice's CLI loader does no field or index work. Driving
LibreOffice over a socket instead lets this script call
``index.update()`` explicitly, so the PDF carries real TOC page numbers
— the1.7 promise. The script is deliberately standalone (stdlib + uno,
no lekhika imports): any Python carrying the python3-uno bindings can
run it by path, which is usually the *system* python rather than the
one hosting the app.

Usage: uno_pdf.py <in.docx> <out.pdf> <soffice> <workdir>
The LibreOffice profile and log live under workdir; the caller owns
that directory (and cleans it up), so hard-killed runs leak nothing.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
import traceback
from pathlib import Path


def main() -> None:
    if len(sys.argv) != 5:
        raise SystemExit(
            "usage: uno_pdf.py <in.docx> <out.pdf> <soffice> <workdir>")
    try:
        import uno
        from com.sun.star.beans import PropertyValue
    except ImportError as error:
        # The caller probes for uno before getting here; this is the
        # belt for probes that raced the environment.
        raise SystemExit(f"no-uno: {error}")

    document_path = Path(sys.argv[1])
    pdf_path = Path(sys.argv[2])
    soffice = sys.argv[3]
    workdir = Path(sys.argv[4])
    profile = workdir / "profile"
    profile.mkdir(parents=True, exist_ok=True)
    log_path = workdir / "soffice.log"

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [soffice,
             f"-env:UserInstallation={profile.as_uri()}",
             "--headless", "--norestore", "--nofirststartwizard",
             "--invisible",
             f"--accept=socket,host=127.0.0.1,port={port};urp;"],
            stdout=log, stderr=subprocess.STDOUT,
        )

    document = None
    desktop = None
    try:
        local_context = uno.getComponentContext()
        resolver = local_context.ServiceManager.createInstanceWithContext(
            "com.sun.star.bridge.UnoUrlResolver", local_context)
        context = None
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                context = resolver.resolve(
                    f"uno:socket,host=127.0.0.1,port={port};urp;"
                    "StarOffice.ComponentContext")
                break
            except Exception:
                if process.poll() is not None:
                    raise SystemExit(
                        "LibreOffice exited before it was ready: "
                        + log_path.read_text(encoding="utf-8")[-1000:])
                time.sleep(0.2)
        if context is None:
            raise SystemExit(
                "could not reach LibreOffice over the socket within 20s; "
                "profile log: " + log_path.read_text(encoding="utf-8")[-1000:])

        def property_value(name, value):
            item = PropertyValue()
            item.Name = name
            item.Value = value
            return item

        desktop = context.ServiceManager.createInstanceWithContext(
            "com.sun.star.frame.Desktop", context)
        document = desktop.loadComponentFromURL(
            uno.systemPathToFileUrl(str(document_path)), "_blank", 0,
            (property_value("Hidden", True),),
        )
        # The TOC: Word's field arrives as a real content index, and
        # only an explicit update rebuilds it — the CLI never does.
        indexes = document.getDocumentIndexes()
        for index in range(indexes.getCount()):
            indexes.getByIndex(index).update()
        document.storeToURL(
            uno.systemPathToFileUrl(str(pdf_path)),
            (property_value("FilterName", "writer_pdf_Export"),),
        )
    except SystemExit:
        raise
    except Exception:
        raise SystemExit(traceback.format_exc(limit=8))
    finally:
        if document is not None:
            try:
                document.close(False)
            except Exception:
                pass
        if desktop is not None:
            try:
                desktop.terminate()
            except Exception:
                pass
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()


if __name__ == "__main__":
    main()
