# Pitfalls

Facts that stay true after the task that found them: a tool quirk, a library trap, a trap in this code, what this
machine allows. Read the contents block above when a session starts; search the rest when you need it. One entry per
fact, under its section:

    #### <the fact, in one line>
    Where: <the files, functions, libraries and commands it concerns>
    Symptom: <the error text or wrong behavior you see when you hit it>
    <a few lines: why, and what to do instead; the URL if it came from the web>

Add `### <library or module>` subsections as needed. Keep the file true: merge duplicates, correct or delete an entry
that is no longer true (git keeps the history).

## Agent tools and harness

The read, edit and bash tools, the driver, the loop and its checks.

## This machine and its environment

Installed software and versions, paths, ports, network, what is blocked.

## Libraries and frameworks

One `### <library>` subsection per library or tool, with its version.

## This codebase

One `### <module or service>` subsection per part of this project. Say in `Where:` which other parts depend on it:
a trap in one part often shows up in another.
