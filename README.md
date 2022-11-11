# smallfactory
PLM (product lifecycle management) tooling focussed on simplicity and ease of use.

The project aims to provide a low-overhead PLM system through a basic command line tool, and opinionated conventions for managing various aspects of the product lifecycle.

Core tenets:
  * Simple infrastructure: No need to run servers or other infrastructure, restrict overhead to a single command line tool.
  * Utilize git for heavy lifting. I.e. everything necessary is stored in git, and users can access and act on that data outside directly if necessary.
  * Build simply, and focus on maintaining backward compatibility.
  * Highly opinionated conventions, to safe users from dealing with designing and wrangling custom workflows.