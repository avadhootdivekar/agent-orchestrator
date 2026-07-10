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

## Catching all output from agents
I very very much suspect that currently - if agent runs multiple turns - only last output / error is captured. Output from prior turns is lost. I want to CAPTURE ALL THE OUTPUT/ERRORS from Agents across all turns. This will be available for observability and tracability. 

## Unit / integration / e2e tets assurance
Ensure that accurate, meaningful and good coverage tests acros all - unit / integration / e2e tests are added and those also cover recently developed fatures as well as above asks like - 
1. Branching, Circuit breaker, loops, logging, out/err capture etc. 

**For e2e tests- write them as close to end user level as possible** 


Ensure to update /add the playground examples with the latest syntax and latest features. Keep at least two examples separate which can demonstrate very simple basic workflows for basic users. 




--- 

# Old Ask


## Input 2 
## Complete the @ad/tickets/E-rc7k2v-run-control-routing-breakers epic

Use architect or other subagents as relevant and drive the epic to completion fully. 



## Input 1 
**NOTE- All of the following points - may be clubbed under single epic or create different epics - depending on your judgement**
For all of the below tasks - whichever you start with - DO NOT jump into full coding / development. In current session  - ONLY provide the design drafts and HLD, ADR, scope/requirements. LLD task breakdown etc will be handled seperately.  


## DAG With multiple possible endpoints and circuit breaker. 
1. For our ao workflows - I want to follow philosophy that there may not necessarily be single endpoint task. e.g. workflow may start with single prompt, but based on prompts contents, it may follow the workflow path of epic or task or bug or documentation, and in each case - it may pass through different set of tasks and end on different endpoint tasks. Essentially its multiple workflwos clubbed into songle file. 
2. I want to add acircuitbreaker option. In some cases- it may be worthwhile to fail or stop the whole workflow depending on some specifi  conditions. Also outline what all possible conditions can be there for circuit breaking. 

## Global parameterd / settings for workflows and ao in general. 
1. Set default model and efforts etc at different levels **correct me** - 
    1. AO cli / env variable/ config file etc.
    2. Workflow file 
    3. Current repo / working directory 
2. What are the concerns / pitfalls/potentially confusion points if we do so? Or we dont need to give so much configurability and flexibility? I dont want to get in awkward position of conflicting with ourselves because of many different configurations - and just trying to see which configuration is actually taking ffect. There is one more possible approach - and that is to ALWAYS explicitly define the moedl/efforts or such configurations in workflows. They may have default values - or may at least explicitly say that they are picking values from its predecessor - like config file or env variable etc. 


## Further granular breakown of tasks in existing workflow 
1. In workflow defined in @/usr/avadhoot/mounted/ao-runner-finplan/workflows/epic-runner/new-epic-run.sh , I want the developer and unit testers to breakdown tasks further in more granular level. 
    1. I want to optimistically achieve a state where each independent task in the dynamic generated tasks will never reach the full context utilization state - BUT WITHOUT loosing the whole project context and sight of end goal. Typically what happens in development tasks is - agent session keeps running for quite some time - 10-40/50 min and in that session - context may need to be autocompacted multiple times because of reaching the 200k or some context limit. So instead of it having to go to auto compaction, I will like to breaakdown the tasks in multiple stpes early on - so that each steps or set of steps can be taken by independent agent sesions - which will probably complete there tasks in less than 100 or 150 tokens. This is based on premise and assumption that many times in long running sessions - the higher context is not because of core logic / understanding data but more so because of multiple tool calls and the verbose code being present. That may not be really required for agent to work efficiently and just summary of those functions might very well serve the purpose. By breaking the task in multiple tseps - we allow agents to have limited context and save budget. 
    2. My end goals are - Have much more accuracy and much less hallucination. Agent should be able to complete task with very very good quality without end user intervention. Preferrably save budget also in doing so (secondary). 
