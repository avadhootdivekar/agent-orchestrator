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

## Issues in generated workflows and other files 
1. You mentioned in @workflows/scenario-dsl-review/README.md that you have added- model, effort fields in the @specs/agents.json file. However I DO NOT see those fields. I very much suspect that you have NOT updated this file. Please correct me if wrong. 
2. Provide more examples / explanation for how to add dynamic tasks  
    1. e.g. if reviewer wants to span more reviewers for detailed review of some particular modules or something dynamically - how to do that
    2. If reviewer actually wants to invoke developer to do code changes and then tester to actually build - deploy- test changes , how can reviewer dynamically create the required workflows / inject tasks? Provide with clear examples. 
    




--- 

# Old Ask

## input 1 

## Starting examples for new users
Give an example of how to create a new workflow with detailed steps. VERY VERY likely some examples and guidelines readme might already exist for this - update that as per latest commands / workflow syntax.

## Specific template / usecase for me 
1. Give me a readme with detailed steps of how to define workflow for my specific case - 
    1. dir - `/home/avadhoot/mounted/usr-volume/ao-runner-finplan` 
    2. This ao directory contains a finplan repo working on financial app websiet for planning / forecasting etc. 
    3. Workflow needs to be defined for - 
        1. Pull latest master. Stash if any unsaved / uncommited changes there. 
        2. review the current DSL for the sccenario creation using `reviewer` agent which is already defined in the finplan repo in the said diretory - using opus. 
        3. Get the review document in the md file with detailed review and action plan. 
    4. Also add detailed steps for how to execute this workflow and get the desired output. 



