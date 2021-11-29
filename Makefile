FLAKE8?=	flake8
MYPY?=		mypy
ISORT?=		isort
PYTHON?=	python3
TWINE?=		twine

lint: flake8 mypy isort-check

flake8::
	${FLAKE8} ${FLAKE8_ARGS} *.py

mypy::
	${MYPY} ${MYPY_ARGS} *.py

isort-check::
	${ISORT} ${ISORT_ARGS} --check *.py

isort::
	${ISORT} ${ISORT_ARGS} *.py

sdist::
	${PYTHON} setup.py sdist

release::
	rm -rf dist
	${PYTHON} setup.py sdist
	${TWINE} upload dist/*.tar.gz
