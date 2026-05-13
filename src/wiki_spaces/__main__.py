"""`python -m wiki_spaces` entry — delegates to cli.main()."""

import sys

from .cli import main

sys.exit(main())
