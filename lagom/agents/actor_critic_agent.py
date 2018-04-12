import torch
import torch.nn.functional as F
from torch.autograd import Variable
from torch.distributions import Categorical

from lagom.agents.base import BaseAgent
from lagom.core.preprocessors import Standardize

class ActorCriticAgent(BaseAgent):
    """
    Actor-Critic with value network
    """
    def __init__(self, policy, optimizer, lr_scheduler, config):
        self.policy = policy
        self.optimizer = optimizer
        self.lr_scheduler = lr_scheduler
        
        super().__init__(config)
        
    def choose_action(self, obs, mode):
        assert mode == 'sampling' or mode == 'greedy'
        
        out_policy = self.policy(obs)
        # Unpack output from policy network
        action_probs = out_policy['action_probs']
        state_value = out_policy['state_value']
        
        # Create a categorical distribution
        # TODO: automatic distribution select according to action space
        action_dist = Categorical(action_probs)
        # Calculate entropy of the policy conditional on state
        entropy = action_dist.entropy()
        # Calculate perplexity of the policy, i.e. exp(entropy)
        perplexity = action_dist.perplexity()
        
        if mode == 'greedy':  # greedily select an action, useful for evaluation
            action = torch.argmax(action_probs, 1)
            logprob_action = None  # due to greedy selection, no log-probability available
        elif mode == 'sampling':  # sample an action according to distribution
            action = action_dist.sample()
            logprob_action = action_dist.log_prob(action)  # calculate log-probability
            
            
            #print(f'#######{action_probs}')
            #print(f'!!!!!!!{action.item()}')
        
        # Dictionary of output data
        output = {}
        output['action'] = action
        output['logprob_action'] = logprob_action
        output['state_value'] = state_value
        output['entropy'] = entropy
        output['perplexity'] = perplexity

        return output
        
    def learn(self, batch):
        batch_policy_loss = []
        batch_value_loss = []
        batch_entropy_loss = []
        batch_total_loss = []

        for episode in batch:  # Iterate over batch of episodes
            # Get all returns
            Qs = episode.all_returns
            # Get all values
            Vs = episode.all_info('state_value')
            # Get all action log-probabilities
            log_probs = episode.all_info('logprob_action')
            # Get all entropies
            entropies = episode.all_info('entropy')
            
            # TODO: testing standardization before or after computing advantage estimation
            # Calculate advantage estimation
            Qs = Standardize().process(Qs)
            
            # Calculate losses
            policy_loss = []
            value_loss = []
            entropy_loss = []
            
            # iterate over time steps
            for logprob, V, Q, entropy in zip(log_probs, Vs, Qs, entropies):
                advantage_estimate = Q - V.item()
                policy_loss.append(-logprob*advantage_estimate)
                value_loss.append(F.mse_loss(V, torch.Tensor([Q]).unsqueeze(0)).unsqueeze(0))
                entropy_loss.append(-entropy)
            
            # Sum up losses for each time step
            policy_loss = torch.cat(policy_loss).sum()
            value_loss = torch.cat(value_loss).sum()
            entropy_loss = torch.cat(entropy_loss).sum()
            
            # Calculate total loss
            total_loss = policy_loss + self.config['value_coef']*value_loss# + self.config['entropy_coef']*entropy_loss
            
            # Record all losses for current episode
            batch_policy_loss.append(policy_loss)
            batch_value_loss.append(value_loss)
            batch_entropy_loss.append(entropy_loss)
            batch_total_loss.append(total_loss)
            
        # Average total loss over the batch
        # TODO: keep track of new feature to cat zero dimensional Tensor
        batch_total_loss = [total_loss.unsqueeze(0) for total_loss in batch_total_loss]
        loss = torch.cat(batch_total_loss).mean()
        
        # Zero-out gradient buffer
        self.optimizer.zero_grad()
        # Backward pass and compute gradients
        loss.backward()
        # Clip gradient norms if required
        if 'max_grad_norm' in self.config:
            nn.utils.clip_grad_norm(self.policy.parameters(), self.config['max_grad_norm'])
        # Update learning rate scheduler
        #self.lr_scheduler.step()
        # Update for one step
        self.optimizer.step()
        
        # Output dictionary for different losses
        output = {}
        output['loss'] = loss
        output['batch_policy_loss'] = batch_policy_loss
        output['batch_value_loss'] = batch_value_loss
        output['batch_entropy_loss'] = batch_entropy_loss
        output['batch_total_loss'] = batch_total_loss

        return output