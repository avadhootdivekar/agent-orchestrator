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

Create the new epic for following - "Installable for the AO (Agent orchestrator) tool"
1. User should be able to install this AO tool and MUST be able to run AO tool with different set of repos independently with different configurations (like agents/workflows etc.) 
2. Much preferred one click installation - like some bash script or makefiles etc. If we can later source this file on web - we can have sometinhg like - `www.mysite.com/install.sh | bash` or something of the sort. If same script can not be used for public install and current install,  for public fetch we can have different script later.

Confirm with user if any decisions required - else proceed with implementation. 