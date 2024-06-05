PYTHON = python3
SCRIPT = generate_data.py
USER_FILE = source/5users_id.txt
TRANSACTION_PATTERN = source/200transactions*_id.txt

all: generate

generate:
	$(PYTHON) $(SCRIPT) -files 5

clean:
	rm -f $(USER_FILE) $(TRANSACTION_PATTERN)

.PHONY: all generate clean
