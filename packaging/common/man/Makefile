# Man Pages Build System

# Targets
MAN_SOURCES := $(wildcard *.md)
MAN_PAGES := $(MAN_SOURCES:.md=.1)
MAN_GZIPPED := $(MAN_PAGES:%=%.gz)

.PHONY: all clean help

all: $(MAN_GZIPPED)

# Convert Markdown to troff man page format
%.1: %.1.md
	pandoc -s -t man -o $@ $<

# Compress man pages
%.1.gz: %.1
	gzip -9 -c $< > $@

clean:
	rm -f $(MAN_PAGES) $(MAN_GZIPPED)

help:
	@echo "Man page build targets:"
	@echo "  all     - Build all man pages (default)"
	@echo "  clean   - Remove generated files"
	@echo ""
	@echo "Requirements:"
	@echo "  - pandoc (for Markdown to troff conversion)"
	@echo "  - gzip (for compression)"
	@echo ""
	@echo "Install pandoc:"
	@echo "  Fedora/RHEL: dnf install pandoc"
	@echo "  Debian/Ubuntu: apt install pandoc"
	@echo "  macOS: brew install pandoc"
