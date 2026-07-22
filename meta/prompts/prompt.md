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

1. Add the benchmark tests-  
    1. Analyze, survey available benchmarks and tests for checking quality, accuracy, time, cost of the AI agent's soultion and problem solving - especially for development purpose.
    2. Integrate and Use the benchmarking tool to compare the efficiency of our ao tool vs laude sonnet vs claude opus on the given benchmarks. 
    3. Addd the script / make recipe to runb the benchmarks and publish/add results to pre configured directory and add to git for result preservation. 
    4. Its preferred if we benchmarks (same or different) can later be configured for non-software development benchmarking also. e.g. can our ao tool be used for efficient biology problem solving, physics, marketing, legal etc. If we want to later add benchmarks for those non-software development also - put our benchmarking framework configurable that way - of feasible and not too far stretched / overload. 
2. Once framework done - run benchmark of our workflow - epic/bug/ etc against claude sonnet and opus and provide me results. 


--- 

# Old Ask

## Input 1 


## Agent based monitoring and self healing for workflows 
Also check - disallowed tools -> monitoring for claude -p mode. This is supposed to wait for background command to complete - but in '-p' mode it just exits - is that right understanding? 
Also note - thsi looks like specilization / customization for claude - not a general technique - I WANT to handle it - but see the bestt place to keep it as it may not be general and may be model / provider specific configuration - You decide.
Also capture what other tools we may nee to disable in claude -p mode or for the kind of run / tooling we are having and make necessary changes. 


## Input 2

Can I turn my tool into runtime agent - like claude? Where I can do backend API calls to any LLM / AI Agnet - like grok / deepseek/gemini/openai etc. 
Get them to respond in specific predetrmined structured format - like json/yaml or something and execute tool calls on their behalf and also make file edist etc? 

1. Is it doable? What will be scope / esstimates to get basics working - for at least development cycle - grep/build/eexecute/ file edits / saves / got commands etc? 
2. Is it worth it? Is it any value addition? Why would anyone use this tool over claude/aider etc? Go through whole design / requirements / scope and understand if its worth it as a product. 

NOTE - This is PLAN and deliberation only - do NOT do any changes. You can only add documentation as md files or something. 
