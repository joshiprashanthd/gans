!pip install gitpython

import tensorflow as tf
from tensorflow.keras.preprocessing.text import Tokenizer
from tensorflow.keras.preprocessing.sequence import pad_sequences
import numpy as np
import matplotlib.pyplot as plt
from PIL import Image
from git import Repo

import re
import json
import time
import os


class config:
    IMG_HEIGHT = 64
    IMG_WIDTH = 64
    CHANNELS = 3
    EPOCHS = 5000
    BATCH_SIZE = 64
    LATENT_DIM = 100
    LEARNING_RATE = 0.00005
    BETA_1 = 0.5
    
    MAX_LEN = 20
    NUM_WORDS = 1600
    
    LOG_INTERVAL = 500
    SAMPLE_INTERVAL = 200
    

class EmotiGAN:
    def __init__(self):
        
        self.image_shape = (config.IMG_HEIGHT, config.IMG_WIDTH, config.CHANNELS)
        self.kernel_init = tf.keras.initializers.RandomNormal(stddev=0.02)
        self.loss_func = tf.keras.losses.BinaryCrossentropy(from_logits=False)
        self.optimizer = tf.keras.optimizers.Adam(learning_rate=config.LEARNING_RATE, beta_1=config.BETA_1)
        
        print("Fetching Dataset...")
        self.train_images, self.train_labels = self.fetch_dataset()

        print("\tTrain Images Shape : ", self.train_images.shape)
        print()

        print("Building Vocabulary...")
        self.word_index, self.train_sequences, self.padded_sequences = self.build_vocab()
        print("\tWord Index Length : ", len(self.word_index))
        print("\tPadded Sequences Shape : ", self.padded_sequences.shape)
        print()

        print("Fetching Word2Vec Data...")
        self.data = self.fetch_data()
        print()

        print("Initializing embedding...")
        self.embedding = self.init_embedding()
        print()

        print("Building Generator...")
        self.generator = self.build_generator()
        print()
        
        print("Building Discriminator...")
        self.discriminator = self.build_discriminator()
        print()
        
        self.generator_losses = []
        self.discriminator_losses = []
        
    def build_generator(self):
        noise_input = tf.keras.Input(shape=(config.LATENT_DIM,))
        label_input = tf.keras.Input(shape=(config.MAX_LEN,), dtype='int32')
        
        model = tf.keras.Sequential([
            tf.keras.layers.Dense(8 * 8 * 512, input_dim=config.LATENT_DIM * 2),
            tf.keras.layers.Reshape((8, 8, 512)),
            tf.keras.layers.Conv2DTranspose(256, 4, strides=1, padding='SAME', use_bias=False, kernel_initializer=self.kernel_init),
            tf.keras.layers.BatchNormalization(),
            tf.keras.layers.Activation('relu'),
            tf.keras.layers.Conv2DTranspose(128, 4, strides=2, padding='SAME', use_bias=False, kernel_initializer=self.kernel_init),
            tf.keras.layers.BatchNormalization(),
            tf.keras.layers.Activation('relu'),
            tf.keras.layers.Conv2DTranspose(64, 4, strides=2, padding='SAME', use_bias=False, kernel_initializer=self.kernel_init),
            tf.keras.layers.BatchNormalization(),
            tf.keras.layers.Activation('relu'),
            tf.keras.layers.Conv2DTranspose(3, 4, strides=2, padding='SAME', use_bias=False, kernel_initializer=self.kernel_init),
            tf.keras.layers.Activation('tanh')
        ])
        
        # getting word2vec embedding vector
        embedding_output = self.embedding(label_input)
        embedding_output = tf.keras.layers.Lambda(lambda tensor: tf.math.reduce_sum(tensor, axis=1))(embedding_output)
        embedding_output = tf.keras.layers.Dense(100)(embedding_output)

        model_input = tf.keras.layers.concatenate([noise_input, embedding_output])

        fake_image = model(model_input)
        return tf.keras.Model([noise_input, label_input], fake_image)
    
    def build_discriminator(self):
        image_input = tf.keras.Input(shape=self.image_shape)
        label_input = tf.keras.Input(shape=(config.MAX_LEN,), dtype='int32')
        
        model = tf.keras.Sequential([
            tf.keras.layers.Conv2D(32, 3, strides=1, input_shape=self.image_shape, padding='SAME', use_bias=False, kernel_initializer=self.kernel_init),
            tf.keras.layers.LeakyReLU(0.2),
            tf.keras.layers.Conv2D(64, 3, strides=2, padding='SAME', use_bias=False, kernel_initializer=self.kernel_init),
            tf.keras.layers.BatchNormalization(),
            tf.keras.layers.LeakyReLU(0.2),
            tf.keras.layers.Conv2D(128, 3, strides=2, padding='SAME', use_bias=False, kernel_initializer=self.kernel_init),
            tf.keras.layers.BatchNormalization(),
            tf.keras.layers.LeakyReLU(0.2),
            tf.keras.layers.Conv2D(256, 3, strides=2, padding='SAME', use_bias=False, kernel_initializer=self.kernel_init),
            tf.keras.layers.BatchNormalization(),
            tf.keras.layers.LeakyReLU(0.2),
        ])
        
        embedding_output = self.embedding(label_input)
        embedding_output = tf.keras.layers.Lambda(lambda tensor: tf.math.reduce_sum(tensor, axis=1))(embedding_output)
        embedding_output = tf.keras.layers.Reshape((1, 1, 100))(embedding_output)
        embedding_output = tf.keras.layers.Lambda(lambda x: tf.tile(x, [1, 8, 8, 1]))(embedding_output)
        
        model_output = model(image_input)

        clf_input = tf.keras.layers.concatenate([model_output, embedding_output])
        prediction = tf.keras.layers.Conv2D(512, 3, strides=2, padding='SAME', use_bias=False, kernel_initializer=self.kernel_init)(clf_input)
        prediction = tf.keras.layers.BatchNormalization()(prediction)
        prediction = tf.keras.layers.LeakyReLU(0.2)(prediction)
        prediction = tf.keras.layers.Flatten()(prediction)
        prediction = tf.keras.layers.Dense(1, activation='sigmoid')(prediction)
        
        return tf.keras.Model([image_input, label_input], prediction)
    
    def generator_loss(self, disk_fake_preds):
        return self.loss_func(disk_fake_preds, tf.ones_like(disk_fake_preds))
    
    def discriminator_loss(self, disk_fake_preds, disk_real_preds):
        disk_fake_loss = self.loss_func(disk_fake_preds, tf.zeros_like(disk_fake_preds))
        disk_real_loss = self.loss_func(disk_real_preds, tf.ones_like(disk_real_preds))
        return disk_fake_loss + disk_real_loss
    
    def train_generator_step(self, noise, real_images, real_labels):
        with tf.GradientTape() as tape:
            fake_images = self.generator([noise, real_labels], training=True)
            disc_fake_preds = self.discriminator([fake_images, real_labels], training=True)
            loss = self.generator_loss(disc_fake_preds)
            
        gradients = tape.gradient(loss, self.generator.trainable_variables)
        self.optimizer.apply_gradients(zip(gradients, self.generator.trainable_variables))
        
        return loss
            
    
    def train_discriminator_step(self, noise, real_images, real_labels):
        with tf.GradientTape() as tape:
            fake_images = self.generator([noise, real_labels], training=True)
            disc_fake_preds = self.discriminator([fake_images, real_labels], training=True)
            disc_real_preds = self.discriminator([real_images, real_labels], training=True)
            loss = self.discriminator_loss(disc_fake_preds, disc_real_preds)
        
        gradients = tape.gradient(loss, self.discriminator.trainable_variables)
        self.optimizer.apply_gradients(zip(gradients, self.discriminator.trainable_variables))
        
        return loss
        
    @tf.function
    def train_step(self):
        noise = tf.random.normal((config.BATCH_SIZE, config.LATENT_DIM))
        real_images, real_labels = self.random_images_with_labels()
        
        # training discriminator
        d_loss = self.train_discriminator_step(noise, real_images, real_labels)
        
        # training generator
        g_loss = self.train_generator_step(noise, real_images, real_labels)
        
        return g_loss, d_loss
    
    def train(self):
        for epoch in range(config.EPOCHS):
            g_loss, d_loss = self.train_step()
            
            self.generator_losses.append(g_loss)
            self.discriminator_losses.append(d_loss)

            if (epoch + 1) % config.LOG_INTERVAL == 0:
                self.log_progress(epoch, g_loss, d_loss)
            if (epoch + 1) % config.SAMPLE_INTERVAL == 0:
                self.sample_images(epoch)
            
    def random_images_with_labels(self, size=None):
        indexes = np.random.randint(0, self.train_images.shape[0], size=size if size is not None else config.BATCH_SIZE)
        return self.train_images[indexes], self.padded_sequences[indexes]
    
    def log_progress(self, epoch, g_loss, d_loss, accuracy=None):
        print("Epoch {}/{} :".format(epoch+1, config.EPOCHS))
        print("\t[G Loss - {}]\t[D Loss - {}".format(g_loss, d_loss), end='')
        if accuracy is not None:
            print(" | D Acc - {:.4f}]".format(accuracy))
        else:
            print("]")
            
    def generate_progress_graph(self):
        fig, axes = plt.subplots(1, 2, sharex=False, sharey=False, figsize=(24, 16))
        axes[0].plot(self.generator_losses, color='purple',label='Generator Loss')
        axes[1].plot(self.discriminator_losses, color='b', label='Discriminator Loss')

        axes[0].set_title("Generator Loss")
        axes[1].set_title("Discriminator Loss")

        axes[0].set_xlabel("Epochs")
        axes[1].set_xlabel("Epochs")

        axes[0].set_ylabel("Loss")
        axes[1].set_ylabel("Loss")
        plt.savefig('/content/progress_graph.png', bbox_inches='tight')
        plt.close(fig)
                
    def sample_images(self, epoch):
        rows, cols = 4, 4
        
        noise = tf.random.normal((rows * cols, config.LATENT_DIM))
        _, real_labels = self.random_images_with_labels(rows * cols)
        
        fake_images = self.generator.predict([noise, real_labels])
        fake_images = 0.5 * fake_images + 0.5
        
        fig, axes = plt.subplots(rows, cols, sharex=True, sharey=True, figsize=(10, 10))

        count = 0

        for i in range(rows):
          for j in range(cols):
            axes[i, j].imshow(fake_images[count, :, :, :])
            axes[i, j].axis('off')
            count += 1
        
        plt.savefig("/content/image_at_{}.png".format(epoch+1), bbox_inches='tight')
        plt.close(fig)
      
    def transform_image(self, image):
        alpha_channel = image[:,:,3]
        rgb_channels = image[:,:,:3]

        # White Background Image
        white_background_image = np.ones_like(rgb_channels, dtype=np.uint8) * 255

        # Alpha factor
        alpha_factor = alpha_channel[:,:,np.newaxis].astype(np.float32) / 255.0
        alpha_factor = np.concatenate((alpha_factor,alpha_factor,alpha_factor), axis=2)

        # Transparent Image Rendered on White Background
        base = rgb_channels.astype(np.float32) * alpha_factor
        white = white_background_image.astype(np.float32) * (1 - alpha_factor)
        final_image = base + white
        return final_image.astype(np.uint8)

    def fetch_dataset(self):
        start = time.time()
        # utilitiy function to the clean the label
        def clean(text):
            text = text.replace("_", " ")
            text = text.replace("-", " ")
            text = re.sub("[1234567890]", "", text)
            return text
        
        git_url = "https://github.com/iamcal/emoji-data.git"
        git_clone_path = "/content/emoji-data/"
        images_dir = "/content/emoji-data/img-google-64/"
        
        print("\tGit Cloning Starts...")
        if not os.path.exists(git_clone_path):
            print("\t\tDowloading Starts...")
            Repo.clone_from(git_url, git_clone_path)
        else:
            print("\t\tPath already exists : {}".format(git_clone_path))

        labels = []
        images = []
        emojis = json.load(open(git_clone_path + "emoji.json", "r"))
        
        for emoji in emojis:
            if emoji['has_img_google']:
                image_path = images_dir + emoji['image']
                image = Image.open(image_path)
                image = np.asarray(image)
                if image.shape[-1] == 4:
                    images.append(self.transform_image(image))
                    labels.append(clean(emoji['short_name']))
        
        print("\tFetched {} image and {} labels".format(len(images), len(labels)))
        end = time.time()
        print("\tTime taken : {:.4f}".format(end - start))
        return np.stack(images, axis=0), labels        
    
    def build_vocab(self):
        tokenizer = Tokenizer(config.NUM_WORDS, oov_token='<OOV>')
        tokenizer.fit_on_texts(self.train_labels)
        sequences = tokenizer.texts_to_sequences(self.train_labels)
        word_index = tokenizer.word_index
        padded_sequences = pad_sequences(sequences, maxlen=config.MAX_LEN, padding='post', truncating='post')
        return word_index, sequences, padded_sequences

    def init_embedding(self):
        label_input = tf.keras.Input(shape=(config.MAX_LEN,), dtype='int32')
        embedding_index = {}
        count = 0

        for line in self.data:
            if count == 0:
                count += 1
                continue
            values = line.split()
            word = values[0]
            coefs = np.asarray(values[1:], dtype=np.float32)
            embedding_index[word] = coefs
        self.data.close()

        print("\tFound {} embedding vectors".format(len(embedding_index)))

        embedding_dim = 100
        count = 0

        embedding_matrix = np.zeros((config.NUM_WORDS, embedding_dim))
        for word, i in self.word_index.items():
            if i < config.NUM_WORDS:
                embedding_vector = embedding_index.get(word)
                if embedding_vector is not None:
                    count += 1
                    embedding_matrix[i] = embedding_vector
        
        print("\tReplaced {} embedding vectors.".format(count))

        del embedding_index

        embedding = tf.keras.layers.Embedding(config.NUM_WORDS, embedding_dim)
        embedding_output = embedding(label_input)
        model = tf.keras.Model(label_input, embedding_output)
        embedding.set_weights([embedding_matrix])
        embedding.trainable = False
        return model

    def fetch_data(self):
        extract_path = "/content/word2vec/"

        if os.path.exists(extract_path):
            print("\t{} already exists".format(extract_path))
        else:
            print("\tExtracting files to {}".format(extract_path))
            with zipfile.ZipFile("/content/40.zip", 'r') as zip_ref:
                zip_ref.extractall(extract_path)

        return open(extract_path + "model.txt", errors='ignore')