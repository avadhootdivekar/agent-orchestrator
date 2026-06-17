# General instructions

This repo is used for agent orchestrator tool / framework. 
Agent orchestrator frameowrk is supposed to - 
1. Orchestrate multiple agent based workflows to drive the tasks to completion. 
2. Define/ orchestrate the dependancy graphs through DAG or some tools and have ability to define requirements, inputs / outputs through artifacts / file paths, cron job style periodic tasks etc. 
3. Orchestrator should run through structured files like json/yaml etc. and dependencies input, output , all metadata should be defined in structured files only. Actual file contents can be md files or source codes or directories or anything for that matter. 
4. Always keep the main thread contet clean as much possible. Spawn subagents for multiple works. 
5. Compact every few iterations - when the context is bloating so that token cost is limited. 


---

# Current Ask is this  - 

## Ask 1 
Please check these few files - `meta/ao/README.md` , `docs-md/guide-epic-walkthrough.md`, `meta/ao/NEW-EPIC.md`
1. These and related docs probably need to change now - that we have installable - locked versions available. 
2. These docs and examples probably dont demonstrate how to work with different repos. These are assuming we are working within AO repo, but actually if I want to update AO repo through AO workflow - AO repo should be a subdirectory in the AO tool (which I am running) directory and repo should be treated as one of the assets right? 

## Ask 2 
1. Can multiple subagents - create the require tasks? because list of input / output files - may not be known at the time of the task creation itself. So how to dynamically update the file list. Is that expected and recommended or not? 
    1. Consider an epic implementation task - at that time - some source code file names , unit test file names may not be exactly known. So how does deveolper working on that task provide these file names to subsequent agetns? I think that task input / output dependencies should also be dynamically configurable. What are your thoughts/ recommendations on this? 
    2. If looks good and not yet implemented - go ahead and implement it.


Ensure that above tasks - if parallel agents are running - dont conflict with each other. 


---

# Old inputs 

## Input 1

Create the new epic for following - "Installable for the AO (Agent orchestrator) tool"
1. User should be able to install this AO tool and MUST be able to run AO tool with different set of repos independently with different configurations (like agents/workflows etc.) 
2. Much preferred one click installation - like some bash script or makefiles etc. If we can later source this file on web - we can have sometinhg like - `www.mysite.com/install.sh | bash` or something of the sort. If same script can not be used for public install and current install,  for public fetch we can have different script later.

Confirm with user if any decisions required - else proceed with implementation. 