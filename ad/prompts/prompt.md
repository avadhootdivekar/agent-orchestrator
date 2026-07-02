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

## Looks Better
1. Looks like ` AO_E2E_REAL_LLM=1 make test-real-llm` is working as expected. Still it failed - but very likely due to limited number of retries or token limit etc. Dont have any concerns here as of now. Thanks. 
2. Can we add the number of retries and Very top level total token limit in the make recipe as configurable? Please provide exact command with how to run wth those configurations. 
    1. The max attempts / retry count is applicable for what - 
        1. Total number of retries across all agents? 
        2. Total number of retries per agent 
        3. Total number of retries only for top level workflow or something else? 
3. In the playground - sum of array example - please update the instructuions for developer and tester to ACTUALLY generate the code files - like .py or .go and actually compile and run them and test them. Current instructions just talk about creating .md files - which is INCORRECT. 
    1. I want to run those playground tests/ examples and create ACTUAL WORKING code files / packages which will give desired output. 



--- 

# Old Ask


## Input 4 
## Enhancements and fixes
1. Still tests are failing - but very likely due to token exhaustion - no need to check much there - except some easy scope to reduce token usage - may be by reducing workflows steps / less number of iterations etc. Our idea is to test the POC / e2e flow here - not to get very good system.
    1. May be 2 levels of tests - 
        1. one light - even with using cluade - will burn even lesser tokens
        2. Heavy - using claude - will burn some more tokens (Still not too much) due to probably more agents / steps / iterations. 

2. DO NOT remove the artifacts/ logs/ agent outputs automatically - in normal ao flows as well as in tests also. 
    1. May be give some clean or some command in ao itself which will remove the stale artifacts from the preconfigured paths and free up space - something like docker prune maybe. 
    2. In tests as well as in normal ao run - DO NOT clean artifacts / output / intermediate files by default - those can be used for audit / debugging purpose. 
    3. Add small note / task /  todo as follows - Specify the intermediate temporary artifacts size limit. When size grows over this - delete stale data. DO NOT delete data in current run anyways. This should happen as part of any noirmal ao run. But to be picked much later - once product stabilized well. 
    4.  Third point is  to just add task/todo comment in code or someplace for tracking - DO NOT implement make any actual code changes now. 
3. Are sufficient readmes, make recipes added to exercise all these test flows and completely test ao e2e-fulle using claude and all? 
4. **Are we providing mechanism to decide specific model / efforts?** We should have the mechanism to specify the model and effort level in workflow - This will greatly allow us to control the cost and performance. Add this now. I assume this should be 1/2 ticket change only. If you think this is bigger and requires epic - do that - design -refview - get approval by user - implement. But most likely epic not required - then you can directly implement with some subagent. 
5. In playground examples/tests - use the haiku or at most sonnet agent. DO NOT use opus. Use mediumm efforts.  



## Input 3 
## Tests failing
1. Tests are still failing - however reason seems something different - probably agent output not correct format or some api failure or something. 
2. Please check - RCA - do the fix. 
    1. Actualy drive and test the LLM based test - may be using subagent. 
    2. Ensure tests using LLM actually pass correctly. Earlier you said its passing - but now its failing. Probably take more relaxed constraints for timeout - if that matters - like 10min instead of 2 min. etc . but dont burn too many tokens. Upto $20 total is ok. 
    3. ONLY after TRUE test verification with LLM get back to me. 


## Input 2 

1. Tests failing - run yourself with CLAUDE and check why. Do RCA - 
2. Likely root cause seems to be directory access restrictions. Please check logs attached at the end. 
    1. If directory access is indeed issue -> Have some tmp directory - which is gitignored under playground itself or somehwree correct location in repo. So cluade has the access and no issues of failure as well as no nee dto give unrestricted access. 
3. After doing your changes - You yourself - (maybe with subagent) - check that the tests are running clean with actual claude interface and getting the desired output. 
<Post input update - this was due to out of directory acecss restrictions only. >


## Input 1 


## E2E Tests addition
**Add new e2e tests covering full paths from absolute boundaries from user perspective to core engine** 
e.g. invoke the ao tool and check if workflows working as expected, check if loggings are generated as expected, check if epics / tasks / outputs are created as expected. 
Create following types of tests 
1. Fixture based tests
2. Reproducible tests - same set of inputs each time, the expectations are also deterministic. as AO runs on LLM, we may not check exacrt contents as those may change each time even for same set of inputs - so in deterministic tests - we should just test the file neames / paths are exactly as expected etc - which will always be reproducible. 
3. Fuzzy / undeterministic tests  - these may test for different set of random inputs OR even of same set of ibputs - where the output may change each time. 
4. performance and correctnes tests. 


Test Areas: 
1. Workflow and DAG, dependencies
2. Token computation / assumptions 
3. Dynamic inputs / dependency specification
4. Logging
5. Agent output being captured for individual steps / tasks. 
6. CLI and flags being exercised correctly e2e. 

### Potentially track the reproducible problem statements. 
1. Maybe create a directory like playground or something in current repo. 
2. Use thta directory to run different examples / workflows using ao. Some directories within playground can be like - sum-of-array, student-data, sorting etc. 
3. these examples should be simple problem statements - that will test the workflows e2e - but should not consume too many tokens to test. Yes there would be burn - but we will tailor the problems to have the low burn rate. 
4. For each of these workflows - we should define workflows like - 
    1. architect - design (consider that tasks should be able to breakdown) - HLD + LLD + ADR
    2. architect - breakdown the tasks based on design (no design here) 
    3. Reviewer - design review, any missed concerns, ambiguity, tasks and interfaces and test fixures, contracts defined clearly and meaningful. 
    4. For each task -
        1. developer - work on and cmoplete tasks. Dont write unit tests or anything - only busines loggic and relevant integration. 
        2. Test Write - Write the unit / integration tests. Consider design time test fixtures. Write tests by considering user flows and expected outputs - dont bend tests to just pass the business logic. 
        3. Reviewer - review individual tasks - also against design. 
    5. Final reviewer - Once all tasks are complete - review final work - also against design check if any misses. Create new tasks of found bugs/ reviewes etc and take one more round of dev/review. 


We can have some playground examples with some easy problem statements like 
1. Sort the elements in given array 
2. Find the sum of elements in given array 
3. Write functions  methods to work on student data
    1. Given array of students - with name/age/standard/scores - methods should return - students within given age / score range etc. 


These playground problem statements -  can have workflows defined - by ao repo developer - however those workflows will be executed each time - as part of e2e test of ao repo itself and we will check if ao repo / latest updated code is correctly driving those workflows to completion and all new features are working correctly. 


