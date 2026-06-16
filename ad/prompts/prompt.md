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


How do I verify that given epic is completed and to what extent?  - `ad/tickets/E-m2k9pa-orchestrator-mvp-a/EPIC.md`. 

Can you help me setup following: 
1. Use the agent orchestrator to develop agent orchestrator itself. 
    1. Is it possible? If we want to do that - do I need to do installation or something beforehand? Else when we change code - my agent orchestrator may break if its running from the same source? 
2. Do required setup, DAG / workflow definitions etc. 
3. I would recommend paths and workflows as in subsequent sections. 



## Paths 
Have the `meta/ao/` where agent orchestrator related definitions will live. 
Rest will depend on or need to be adopted the agent Orchestrator (AO) naming conventions and styles if any. 
e.g. - `meta/ao/epics` , `meta/ao/epic-1/inputs` , `meta/ao/epic-1/outputs` and so on. 

Agents in `.claude/agents` 


## Build epic for agent orchestrator epic creation and completion.
Define workflows for : 
1. Epic requirement gathering and consolidation - from user prompt file - draft clear epic requirements, goals, scope etc. Do market survey and provide standard functionailities and requirements etc. 
2. Epic design (Opus) 
    1. Clear Scope / goals etc (if required again) - as prior model might be lower model? 
    2. ADR, HLD, LLD 
3. Task breakup along with sufficient details and estimates. Clear task dependencies (also should be added to workflow or some structured files for completion and driving the epic. )
4. Implementing the tasks
    1. Dev 1 - Complete and implement task. 
    2. Dev 2 - Write the unit tests - based on the design and task (not based on the task implementation). Unit test should test business logic rigourosly- should not just bend unit test to allow any logic to pass. 
    3. Reviewer - review Design vs task vs unit tests and comments + pass/fail 
    4. Reiterate till reviews pass Maxx 2 cycles.  
6. End review - 
    1. Critic review for full epic + task implementation 
    2. Ensure sufficient doc updates, makefiles/scripts added. Full unit . integration, e2e tests added. 
    3. Architect review for full epic + task implementation. Also address review comments as appropriate and again run the task implement pipeline or complete the epic. 


Add the workflows for above in separate preferably single directory so that its clear and can be easily maintained. 
Once implemented - tell me how can I give requirements for new epic and drive it to completion. 

