SPHINXBUILD   ?= sphinx-build
SOURCEDIR     = .
BUILDDIR      = _build

.PHONY: html clean live

html:
	$(SPHINXBUILD) -b html $(SOURCEDIR) $(BUILDDIR)/html

clean:
	rm -rf $(BUILDDIR) autoapi

live:
	sphinx-autobuild $(SOURCEDIR) $(BUILDDIR)/html
