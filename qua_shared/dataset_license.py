"""Single source for the QUA timing-data licence identity.

The data (GH releases, HF dataset, future API) ships under the custom QUA
Dataset License (``LICENSE-DATA``); the code is Apache-2.0 (``LICENSE``).
Every release manifest, changelog footer, and preview payload reads from here.
"""

DATA_LICENSE_ID = "qua-dataset-1.0"
DATA_LICENSE_NAME = "QUA Dataset License 1.0"
#: Repo-root filename; uploaded to each GH release as ``LICENSE``.
DATA_LICENSE_FILE = "LICENSE-DATA"
DATA_LICENSE_URL = (
    "https://github.com/QUD-Technologies/quranic-universal-audio/blob/main/LICENSE-DATA"
)
