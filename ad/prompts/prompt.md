# General instructions

This repo is used for agent orchestrator tool / framework. 
Agent orchestrator frameowrk is supposed to - 
1. Orchestrate multiple agent based workflows to drive the tasks to completion. 
2. Define/ orchestrate the dependancy graphs through DAG or some tools and have ability to define requirements, inputs / outputs through artifacts / file paths, cron job style periodic tasks etc. 
3. Orchestrator should run through structured files like json/yaml etc. and dependencies input, output , all metadata should be defined in structured files only. Actual file contents can be md files or source codes or directories or anything for that matter. 
4. Always keep the main thread contet clean as much possible. Spawn subagents for multiple works. 
5. Compact every few iterations - when the context is bloating so that token cost is limited. 
6. **correct me** keywird -> Whenever I ask **correct me** - have a deep and thorough understanding of question and contex, 
    1. Check if my question is right and relevant 
    2. Seek additional inputs from me if required. 
    3. Any other approaches, alternatives or completely something else that makes this point moot etc. 
    4. Suggest alternatives if any - upto 3 top alternatives.  
7. Checklist to be presented at the end 
    1. Given tasks all complete
    2. Build and unit tests pass
    3. Any regression in build / unit / integration / e2e tests? Is that verified against the baseline quantitatively - Dont give assumed numbers. 
    4. Test coverage maintained or increased. 
    5. Only Applicable for epic or large refactors, else mark as NA - 
        1. Design doc updated to match the current implementation
        2. ADR updated 
        3. examples / playground code updated 


---

# Current Ask is this  - 

## Add accurate rate computation at the end of run / tasks
1. I see that we are already capturing the tokens input / output / cost usd etc from claude directly which are reliable. I suspect that we may not be adding those numbers for the same task.
    1. If same task takes 40 turns, we may be showing the metrices only for the last turn - instead of cumulative 40 turns. Is that the case? I WANT TO SEE FULL CUMULATIVE metrices for each task. This will give more clear picturre. 
    2. SImilarly at the end of workflow - all task stats should be captured and added to the workflow stats - so that I get the total cost / tokens for the full run. These are actual values instead of estimated values. 
    3. Also add the tripwire / circuitbreaker if actual values exceed given thresholds - which can be configurable and are circuit breakers. 
2. Install and upgrade the latest ao in my laptop - here. 


## Update workflow in `/usr/avadhoot/mounted/ao-runner-finplan/`

Update the `workflows/epic-runner/new-epic-run.sh`, `/usr/avadhoot/mounted/ao-runner-finplan/.ao/config.yaml` etc to set the max turns = 200 and actual budget for each task = $3, total cost for the workflow = $100



--- 

# Old Ask

## Input 2 

Ensure good default configurations for `/usr/avadhoot/mounted/ao-runner-finplan/` i.e.  `/usr/avadhoot/mounted/ao-runner-finplan/.ao/config.yaml` and related workflow files. 

Do NOT use Fable. Use Opus or sonnet with sonnet for most tasks - developers / test writer etc. Also other default configs that we need - please set meaningful values, For default model - use sonnet, so when no model is specified - it wont accidently use fable or opus. 


## Input 1 

## Defining good workflow for my separate repo 


### Summary
I have a ao runner configuration / setting at - `/usr/avadhoot/mounted/ao-runner-finplan/`. This directory contains single repo - `fin_plan` which is about financial planning simulation and forecasting. I want to update/create a robust workflow which can be reused again and again to initiate, create and drive to completion, multiple different tasks/epics/bug/fixes/documentation tasks each time I invoke the script. 

### Existing artifacts and references
Check the current epic runner script at - `/usr/avadhoot/mounted/ao-runner-finplan/workflows/epic-runner/new-epic-run.sh` . 
I think its better if we build on existing tools / scripts instead of adding multiple similar codes which may be abandoned later. 
Requirements - 
1. Add the task type - Bug / epic/  task / documentation/ testing
    1. We should have workflows in place which will drive the tawsks to compeltion as per the ticket type. Should use the user prompt and do as much independent work as possible - preferrably not be blocked on user. Only if there is real concern or user input required - break / pause the flow and wait for user input in specific artifcat file or something. 
2. Add the resume option in the script - may need runid from user. 
3. Ensure that tasks are broken in sufficient details that autocompaction would typically not be required. Idea is to both optimize the cost and optimize the accuracy / performance. 
4. Also Add git push as the last step -if its not already tehre. But NOTE - NO FORCE PUSH. Ensure we DONT accidently push to main or Force push and overwrite remote branch etc. If need to rebase or force push etc - need explicit approval from user. Dont do any destructive changes in git. 
5. Priority for correctness and providing ground truths rather than made up claims and assuming things. These should be in general instructions. Make good use of claude subagents that are already defined in fin_plan repo wherever possible. Additional instructions can still be provided as appropriate (does that even make sense?) 

