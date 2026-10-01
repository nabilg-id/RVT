"""ZIP creation (streaming via stdlib zipfile)."""
import zipfile
from pathlib import Path


def create_zip_from_files(files, output_path):
    """Create ZIP from list of (path, arcname) tuples."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(str(output_path), "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path, arcname in files:
            if Path(file_path).exists():
                zf.write(str(file_path), arcname)
    return output_path.stat().st_size


class ZipStream:
    """Streaming ZIP: add files one by one, finalize at end."""

    def __init__(self, output_path):
        self.output_path = Path(output_path)
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self._zf = zipfile.ZipFile(
            str(self.output_path), "w", zipfile.ZIP_DEFLATED
        )

    def add_file(self, file_path, arcname):
        if Path(file_path).exists():
            self._zf.write(str(file_path), arcname)

    def add_buffer(self, data, arcname):
        if isinstance(data, str):
            data = data.encode("utf-8")
        self._zf.writestr(arcname, data)

    def finalize(self):
        self._zf.close()
        return self.output_path.stat().st_size
