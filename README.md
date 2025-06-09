# Barko Agent - Simple Agent


## Getting started

To install this you can either use docker or just install all the dependencies for python and then run the code

### Install with docker

Just in the main directory you can write:

<pre>
    docker-compose up
</pre>

### Install python

Make sure you are using the Python versions 3.9 - 3.12

<pre>
    pip install -r requirements.txt
    python client.py
</pre>


### Set up Barko Agent

Before you run the Agent, you will need to get the `BACKEND_WS_URI`

1. Navigate to https://beta.barkoagent.com/chat
2. Create a new project, and select the `Custom Agent`
3. You will get your unique `uuid4` for that project
4. add the `System Prompt` (we have added sample system prompt for this project in [here](system_prompt.txt))
5. Copy the `uuid4` and save the project.
6. Add you `uuid4` to the .env
7. Run your agent


## Your first prompt

You can test this custom agent by writing:

`Go to barkoagent.com and check that there is a join input`

`Go to barkoagent.com and check that there is a join input, and input a random email but don't sent it`
