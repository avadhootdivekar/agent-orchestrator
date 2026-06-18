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

## Requirements 

**Create new epic and fix following** 

1. Provide an option to limit the token usage (estimated). You suggest best strategy for how this can be handled. 
    1. My suggestion - compute estimated tokens based on inputs provided, output generated etc. Assume computed * 1.3 tokens to have some buffer and have pessimistic computation. 
    2. Preferrably also provide the option to limit  the token usage per min or per 10 min or per hour something. 
    3. So there can be 2 limits - 1. Full limit , 2. Rate limit. both need to be implemented.  
2. Catch the rate / usage limit exhaustion and also when the next quota will be availlable. 
    1. Provide flag to sleep till the next period and continue from that point or stop when quota exhausted. i.e. stop on exhaustion OR wait on exhaustion. 


--- 

# Old Ask



