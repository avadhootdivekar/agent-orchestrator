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

You were already working on it - but got paused. Also note - I have addded the exact string for claude quot exhaustion now - that was not there earlier. 
Please resume and complete. 

## Are we gracefully handlind limit exhaustion and retrying
1. When claude hits the usage limit - how are we handling that? 
    1. I see following few possibilities 
        1. Capture the claude output, identify the limit hit - fallback and retry after some period or exact resource limit end time. This SHOULD BE DONE. 
        2. Consider this as failure and try again till max attempts times . This SHOULD NOT be done. 
        3. Anything else? Tell me. 
    2. Tell me what is happening. 
2. If this mechanism is NOT already there - I want to add the support for running ao across multiple sessions / limit sessions. 
    1. Few scenarios for more understanding for you - 
        1. Claude sets 5 hourly / weekly quota. 
            1. If my orchestrator hits the 5 hr quota - it should NOT fail. 
            2. ao should wait for 5 hr time window to finish, wait for my limit to be replenished and resume once the quota is available. 
            3. User can configure what is the maximum wait time that ao can wait for in case of quota exhastion. e.g. max_wait=8hrs , if quota exhausted at 6AM, even till 2 PM quota remains exhausted - without being able to get any usable window - then ao should fail / stop. If usable window is available before 2 PM - then wait period should reset and wait count should be started only when next quota exhaustion is hit.  
            4. This max wait period should be configurable from CLI , in make recipe as well as config file / env variable. 
3. We had added some configurations like - model/efforts/max attempts/ max turns etc recently
    1. We need to expose them toend user through ALL of the following mnechanisms 
        1. CLI arguments (MUST) 
        2. configuration file (if config file supported)
        3. env variable (if env variables are supported)

If anything is not clear in above - ask me - lets get a cleara plan and implement it if not already in place. 

Exact string for claude session limit - 
```
You've hit your session limit · resets 7:40pm (Asia/Kolkata)
/upgrade to increase your usage limit.
```

here the sesion limit may be replced with weekly limit or something similar. I think the regex should be along lines of - "You've hit.*limit"
Opportunistically parse the quota available time with - "resets (Time + Timezone)". Its possible that in some case this time may not be available - there retry periodically. 


--- 

# Old Ask


