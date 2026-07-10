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

## Add version flag/CLI option 

1. Add CLI option for AO to show version. Version should have optional build time / commit id - when its not built from the tagged source or has any modified files locally. If you have any other suggestions - please provide. 
2. Any other standard option / provision missing to consider this ao cli as industry standard? 


--- 

# Old Ask

