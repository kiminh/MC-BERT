"""MCB_Orig model."""

from torch import nn
import torch.nn.functional as F
import torch

from mcbert.models.layers.visual.attention import AttentionMechanism
from mcbert.models.layers.composition.mcb import MCB
from mcbert.util.mcbtokenizer import MCBDict

class MCBOriginalModel(nn.Module):

    """Class implementing MCBERT Model with visual attention."""


    def __init__(self, vocab_file, vis_feat_dim=2208, spatial_size=7, embd_dim=300, hidden_dim = 2048,
                 cmb_feat_dim=16000, kernel_size=3, bidirectional=False, classification = True ):


        """Initialize MCBertModel."""
        super(MCBOriginalModel, self).__init__()
        self.vis_feat_dim = vis_feat_dim
        self.spatial_size = spatial_size
        self.hidden_dim = hidden_dim
        self.cmb_feat_dim = cmb_feat_dim
        self.kernel_size = kernel_size

        #hint to whatever head uses us - 
        self.output_dim = cmb_feat_dim

        #probably want to do this elsewhere and pass in but...
        dict = MCBDict(metadata=vocab_file)
        vocab_size = dict.size()

        # override the word embeddings with pre-trained
        self.glove = nn.Embedding(vocab_size, embd_dim, padding_idx=0)
        self.glove.weight = nn.Parameter(torch.tensor(dict.get_gloves()).float())

        # build mask  (Or, especially if we don't need EOS/SOS, just make OOV random
        self.embeddings_mask = torch.zeros(vocab_size, requires_grad=False).float()
        self.embeddings_mask[0:4] = 1
        #self.embeddings_mask.resize_(vocab_size, 1)

        if torch.cuda.is_available():
            self.embeddings_mask = self.embeddings_mask.cuda()

        # mask pretrained embeddings
        self.glove.weight.register_hook(
            lambda grad: grad * self.embeddings_mask)

        #each layer (or direction) gets its own part
        lstm_hidden_dim = int(hidden_dim / 2 / (2 if bidirectional else 1))

        self.embedding = nn.Embedding(vocab_size, embd_dim, padding_idx=0) #weight_filler=dict(type='uniform',min=-0.08,max=0.08))
        self.lstm = nn.LSTM(embd_dim*2, num_layers=2, hidden_size=lstm_hidden_dim, batch_first=True, bidirectional=bidirectional, dropout=0.3) #weight_filler=dict(type='uniform',min=-0.08,max=0.08)
        #self.drop = nn.Dropout(0.3)
        #self.layer2 = nn.LSTM(lstm_hidden_dim, hidden_size=lstm_hidden_dim, batch_first=True)  # weight_filler=dict(type='uniform',min=-0.08,max=0.08)

        self.attention = AttentionMechanism(
            self.vis_feat_dim, self.spatial_size, self.cmb_feat_dim,
            self.kernel_size, self.hidden_dim)

        self.compose = MCB(self.hidden_dim, self. cmb_feat_dim)

        # signed sqrt

    def forward(self, vis_feats, input_ids, token_type_ids=None,
                attention_mask=None):
        """Forward Pass."""

        #bit of a hack, but we pass the length through in the token_type_ids
        #just need one per example
        lengths = token_type_ids[:,[0]].squeeze(-1)

        embds = F.tanh(self.embedding(input_ids))
        gloves = self.glove(input_ids)
        inpt = torch.cat((embds, gloves), dim=2)

        lout, (hlayers, _) = self.lstm(inpt)
        #l1out, (hlayers1, _) = self.layer1(inpt)
#        l1out = self.drop(l1out)
        #hlayers1 = self.drop(hlayers1)
        #l2out, (hlayers2, _) = self.layer2(l1out)
        #l2out = self.drop(l2out)
        #hlayers2 = self.drop(hlayers2)

        # sequence_output: [batch_size, sequence_length, bert_hidden_dim]
        # pooled_output: [batch_size, bert_hidden_dim]
        #orig_pooled_output = torch.cat((hlayers1.transpose(0,1), hlayers2.transpose(0,1)), dim=2)

        hlayers = hlayers.transpose(0, 1)
        #print("hlayers:", hlayers.shape)
        #hlayers: torch.Size([4, 2, 1104])
        lstmhids = []
        for i in range(hlayers.shape[1]):
            lstmhids.append(hlayers[:,i,:].unsqueeze(1))
        
        #orig_pooled_output = torch.cat((hlayers[:,0,:].unsqueeze(1),hlayers[:,1,:].unsqueeze(1)), dim=2)
        orig_pooled_output = torch.cat(lstmhids, dim=2)

        #print("l1out:", l1out.shape, "l1hid:", hlayers1.shape)
        #sequence_output = torch.cat((l1out, l2out), dim=2)

        #some tasks require the visual features to be tiled, but we just need a single copy here
        vis_feats = vis_feats[:, 0, :, :].unsqueeze(1)


        #print("before attn: sequence_output:", sequence_output.shape)
        #print("before attn: vis_feats:", vis_feats.shape)
        #print("before attn: orig_pooled_output", orig_pooled_output.shape)

        # batch_size x sequence_length x hidden_dim
        sequence_vis_feats = self.attention(vis_feats, orig_pooled_output)

        #$print("after attn: sequence_output:", sequence_output.shape)
        #print("affter attn: sequence_vis_feats:", sequence_vis_feats.shape)

        # batch_size x seqlen x cmb_feat_dim
        sequence_cmb_feats = self.compose(
            orig_pooled_output, sequence_vis_feats)

        #print("after MCB: sequence_cmb_feats:", sequence_cmb_feats.shape)

        pooled_output = sequence_cmb_feats.squeeze(1)#[:,-1,:]

        #print("pooled_output", pooled_output.shape)

        return sequence_cmb_feats, pooled_output
