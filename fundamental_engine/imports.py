"""Import user-supplied text/PDF into a dated source record; no URL fetching or OCR."""
from pathlib import Path
import shutil
import subprocess
import tempfile


def import_document(metadata, path):
    path=Path(path).resolve()
    if path.stat().st_size>32*1024*1024:
        raise ValueError('Source file exceeds 32 MiB')
    if path.suffix.lower()=='.pdf':
        executable=shutil.which('pdftotext')
        if not executable:
            raise ValueError('PDF import requires the optional pdftotext executable')
        with tempfile.TemporaryDirectory() as temp:
            target=Path(temp)/'extracted.txt'
            try:
                result=subprocess.run([executable,'-enc','UTF-8',str(path),str(target)],
                    stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=20,check=False)
            except subprocess.TimeoutExpired as exc:
                raise ValueError('PDF extraction timed out') from exc
            if result.returncode or not target.exists():
                raise ValueError('PDF text extraction failed; supply a text export')
            if target.stat().st_size>2*1024*1024:
                raise ValueError('Extracted PDF text exceeds 2 MiB')
            body=target.read_text(encoding='utf-8')
    elif path.suffix.lower() in {'.txt','.md'}:
        if path.stat().st_size>2*1024*1024:
            raise ValueError('Text source exceeds 2 MiB')
        body=path.read_text(encoding='utf-8')
    else:
        raise ValueError('Supported source files: .txt, .md, searchable .pdf')
    if not body.strip():
        raise ValueError('No text found; scanned PDFs require an externally reviewed OCR export')
    return {**metadata,'body':body}
